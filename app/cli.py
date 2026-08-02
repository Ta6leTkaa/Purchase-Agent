import argparse
import asyncio
import signal
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import TextIO
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters import provider_registry
from app.core.config import settings
from app.db.session import async_session_maker
from app.domain.mission import Mission, MissionStatus
from app.repositories.identity import IdentityRepository
from app.repositories.mission import MissionRepository
from app.repositories.notification_outbox import NotificationOutboxRepository
from app.repositories.sqlalchemy.identity import SqlAlchemyIdentityRepository
from app.repositories.sqlalchemy.mission import SqlAlchemyMissionRepository
from app.repositories.sqlalchemy.notification_outbox import (
    SqlAlchemyNotificationOutboxRepository,
)
from app.services.clock import utc_now
from app.services.due_mission_processor import (
    DueMissionProcessingResult,
    process_due_missions,
)
from app.services.mission_event_projection import (
    MissionEventProjectionRebuildResult,
    RebuildMissionEventProjection,
)
from app.services.mission_worker import (
    MissionWorkerCycleResult,
    run_mission_worker,
)
from app.services.notification_delivery import (
    RecipientRoutingNotificationAdapter,
    open_notification_delivery_adapter,
)
from app.services.notification_outbox import (
    dispatch_pending_notifications,
)
from app.services.notification_worker import (
    NotificationWorkerCycleResult,
    process_notification_worker_cycle,
)
from app.services.provider_history_rebuild import (
    ProviderHistoryProjectionRebuildResult,
    RebuildProviderHistoryProjection,
)
from app.services.provider_resolver import ProviderResolver


@dataclass(frozen=True)
class CliDependencies:
    session_maker: async_sessionmaker[AsyncSession]
    mission_repository_factory: Callable[[AsyncSession], MissionRepository] = (
        SqlAlchemyMissionRepository
    )
    identity_repository_factory: Callable[[AsyncSession], IdentityRepository] = (
        SqlAlchemyIdentityRepository
    )
    notification_outbox_repository_factory: Callable[
        [AsyncSession], NotificationOutboxRepository
    ] = SqlAlchemyNotificationOutboxRepository
    notification_maintenance_repository_factory: Callable[
        [AsyncSession], SqlAlchemyNotificationOutboxRepository
    ] = SqlAlchemyNotificationOutboxRepository
    provider_resolver: ProviderResolver = ProviderResolver(provider_registry)
    clock: Callable[[], datetime] = utc_now


class StaleMissionRecoveryResult(BaseModel):
    recovered_count: int
    recovered_mission_ids: list[UUID]


class NotificationPruneResult(BaseModel):
    cutoff: datetime
    dry_run: bool
    matched_count: int
    deleted_count: int
    message_ids: list[UUID]


def get_cli_dependencies() -> CliDependencies:
    return CliDependencies(session_maker=async_session_maker)


async def process_due_command(
    limit: int,
    *,
    session_maker: async_sessionmaker[AsyncSession] | None = None,
    dependencies: CliDependencies | None = None,
    mission_repository: MissionRepository | None = None,
    identity_repository: IdentityRepository | None = None,
    current_time: datetime | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output = stdout or sys.stdout
    error_output = stderr or sys.stderr
    resolved_dependencies = _resolve_dependencies(
        dependencies,
        session_maker,
    )
    now = current_time or resolved_dependencies.clock()

    try:
        if mission_repository is not None and identity_repository is not None:
            result = await process_due_missions(
                mission_repository,
                identity_repository,
                now,
                limit=limit,
                provider_resolver=resolved_dependencies.provider_resolver,
            )
        else:
            result = await _process_due_with_database_session(
                resolved_dependencies,
                now,
                limit,
            )
    except Exception:
        error_output.write("Infrastructure error while processing due missions.\n")
        return 1

    output.write(result.model_dump_json() + "\n")
    if result.failed_mission_ids:
        return 1
    return 0


async def recover_stale_command(
    claim_timeout: timedelta,
    limit: int,
    *,
    dependencies: CliDependencies | None = None,
    stderr: TextIO | None = None,
) -> tuple[int, StaleMissionRecoveryResult]:
    error_output = stderr or sys.stderr
    resolved_dependencies = dependencies or get_cli_dependencies()
    current_time = resolved_dependencies.clock()

    try:
        recovered_missions = await _recover_stale_with_database_session(
            resolved_dependencies,
            current_time,
            claim_timeout,
            limit,
        )
    except Exception:
        error_output.write("Infrastructure error while recovering stale missions.\n")
        return 1, StaleMissionRecoveryResult(
            recovered_count=0,
            recovered_mission_ids=[],
        )

    return 0, StaleMissionRecoveryResult(
        recovered_count=len(recovered_missions),
        recovered_mission_ids=[mission.id for mission in recovered_missions],
    )


async def dispatch_notifications_command(
    limit: int,
    *,
    dependencies: CliDependencies | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    resolved = dependencies or get_cli_dependencies()
    output = stdout or sys.stdout
    error_output = stderr or sys.stderr
    try:
        async with open_notification_delivery_adapter(
            output,
            webhook_url=settings.notification_webhook_url,
            webhook_bearer_token=_notification_webhook_bearer_token(),
            webhook_signing_secret=_notification_webhook_signing_secret(),
            webhook_timeout_seconds=settings.notification_webhook_timeout_seconds,
        ) as adapter:
            async with resolved.session_maker() as session:
                result = await dispatch_pending_notifications(
                    resolved.notification_outbox_repository_factory(session),
                    RecipientRoutingNotificationAdapter(
                        adapter,
                        resolved.identity_repository_factory(session),
                    ),
                    resolved.clock(),
                    limit=limit,
                    retry_delay=timedelta(
                        seconds=settings.notification_retry_initial_seconds
                    ),
                    max_retry_delay=timedelta(
                        seconds=settings.notification_retry_max_seconds
                    ),
                    max_attempts=settings.notification_max_delivery_attempts,
                )
    except Exception:
        error_output.write("Infrastructure error while dispatching notifications.\n")
        return 1
    output.write(result.model_dump_json() + "\n")
    output.flush()
    return 1 if result.permanently_failed_count else 0


async def notification_worker_command(
    poll_interval: timedelta,
    limit: int,
    *,
    claim_timeout: timedelta = timedelta(minutes=5),
    dependencies: CliDependencies | None = None,
    stop_event: asyncio.Event | None = None,
    max_cycles: int | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    resolved = dependencies or get_cli_dependencies()
    resolved_stop_event = stop_event or asyncio.Event()
    output = stdout or sys.stdout
    error_output = stderr or sys.stderr

    async def process_cycle() -> NotificationWorkerCycleResult:
        async with open_notification_delivery_adapter(
            output,
            webhook_url=settings.notification_webhook_url,
            webhook_bearer_token=_notification_webhook_bearer_token(),
            webhook_signing_secret=_notification_webhook_signing_secret(),
            webhook_timeout_seconds=settings.notification_webhook_timeout_seconds,
        ) as adapter:
            async with resolved.session_maker() as session:
                return await process_notification_worker_cycle(
                    resolved.notification_outbox_repository_factory(session),
                    RecipientRoutingNotificationAdapter(
                        adapter,
                        resolved.identity_repository_factory(session),
                    ),
                    resolved.clock(),
                    limit=limit,
                    claim_timeout=claim_timeout,
                    retry_delay=timedelta(
                        seconds=settings.notification_retry_initial_seconds
                    ),
                    max_retry_delay=timedelta(
                        seconds=settings.notification_retry_max_seconds
                    ),
                    max_attempts=settings.notification_max_delivery_attempts,
                )

    async def write_result(result: NotificationWorkerCycleResult) -> None:
        output.write(result.model_dump_json() + "\n")
        output.flush()

    async def write_error(error: Exception) -> None:
        del error
        error_output.write("Infrastructure error during notification worker cycle.\n")
        error_output.flush()

    await run_mission_worker(
        process_cycle,
        poll_interval=poll_interval,
        stop_event=resolved_stop_event,
        on_result=write_result,
        on_error=write_error,
        max_cycles=max_cycles,
    )
    return 0


async def prune_notifications_command(
    retention: timedelta,
    limit: int,
    *,
    dry_run: bool = False,
    dependencies: CliDependencies | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    if retention <= timedelta(0):
        raise ValueError("retention must be greater than zero")
    resolved = dependencies or get_cli_dependencies()
    output = stdout or sys.stdout
    error_output = stderr or sys.stderr
    cutoff = resolved.clock() - retention
    try:
        async with resolved.session_maker() as session:
            message_ids = await resolved.notification_maintenance_repository_factory(
                session
            ).prune_delivered_before(cutoff, limit, dry_run=dry_run)
    except Exception:
        error_output.write("Infrastructure error while pruning notifications.\n")
        return 1
    output.write(
        NotificationPruneResult(
            cutoff=cutoff,
            dry_run=dry_run,
            matched_count=len(message_ids),
            deleted_count=0 if dry_run else len(message_ids),
            message_ids=message_ids,
        ).model_dump_json()
        + "\n"
    )
    output.flush()
    return 0


async def worker_command(
    poll_interval: timedelta,
    limit: int,
    *,
    claim_timeout: timedelta = timedelta(minutes=15),
    dependencies: CliDependencies | None = None,
    stop_event: asyncio.Event | None = None,
    max_cycles: int | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    resolved_dependencies = dependencies or get_cli_dependencies()
    resolved_stop_event = stop_event or asyncio.Event()
    output = stdout or sys.stdout
    error_output = stderr or sys.stderr

    async def process_cycle() -> MissionWorkerCycleResult:
        current_time = resolved_dependencies.clock()
        recovered = await _recover_stale_with_database_session(
            resolved_dependencies,
            current_time,
            claim_timeout,
            limit,
        )
        recovered_mission_ids = [
            mission.id
            for mission in recovered
            if mission.status is MissionStatus.waiting
        ]
        stale_failed_mission_ids = [
            mission.id
            for mission in recovered
            if mission.status is MissionStatus.failed
        ]
        processing = await _process_due_with_database_session(
            resolved_dependencies,
            current_time,
            limit,
        )
        return MissionWorkerCycleResult(
            recovered_mission_ids=recovered_mission_ids,
            stale_failed_mission_ids=stale_failed_mission_ids,
            processing=processing,
        )

    async def write_result(result: MissionWorkerCycleResult) -> None:
        output.write(result.model_dump_json() + "\n")
        output.flush()

    async def write_error(error: Exception) -> None:
        del error
        error_output.write("Infrastructure error during Mission worker cycle.\n")
        error_output.flush()

    await run_mission_worker(
        process_cycle,
        poll_interval=poll_interval,
        stop_event=resolved_stop_event,
        on_result=write_result,
        on_error=write_error,
        max_cycles=max_cycles,
    )
    return 0


def main(argv: Sequence[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "process-due":
        raise SystemExit(asyncio.run(process_due_command(args.limit)))
    if args.command == "worker":
        raise SystemExit(
            asyncio.run(
                _run_worker_until_signal(
                    timedelta(seconds=args.poll_interval_seconds),
                    args.limit,
                    timedelta(seconds=args.claim_timeout_seconds),
                )
            )
        )
    if args.command == "dispatch-notifications":
        raise SystemExit(asyncio.run(dispatch_notifications_command(args.limit)))
    if args.command == "notification-worker":
        raise SystemExit(
            asyncio.run(
                _run_notification_worker_until_signal(
                    timedelta(seconds=args.poll_interval_seconds),
                    args.limit,
                    timedelta(seconds=args.claim_timeout_seconds),
                )
            )
        )
    if args.command == "prune-notifications":
        raise SystemExit(
            asyncio.run(
                prune_notifications_command(
                    timedelta(days=args.retention_days),
                    args.limit,
                    dry_run=args.dry_run,
                )
            )
        )
    if args.command == "rebuild-provider-history":
        exit_code, rebuild_result = asyncio.run(rebuild_provider_history_command())
        if exit_code == 0:
            sys.stdout.write(
                "Processed missions: "
                f"{rebuild_result.processed_missions}\n"
                "Processed provider events: "
                f"{rebuild_result.processed_provider_events}\n"
                f"Inserted rows: {rebuild_result.inserted_rows}\n"
            )
        raise SystemExit(exit_code)
    if args.command == "rebuild-mission-events":
        exit_code, mission_event_rebuild_result = asyncio.run(
            rebuild_mission_event_projection_command()
        )
        if exit_code == 0:
            sys.stdout.write(
                "Processed missions: "
                f"{mission_event_rebuild_result.processed_missions}\n"
                "Inserted events: "
                f"{mission_event_rebuild_result.inserted_events}\n"
            )
        raise SystemExit(exit_code)

    exit_code, recovery_result = asyncio.run(
        recover_stale_command(
            timedelta(seconds=args.claim_timeout_seconds),
            args.limit,
        )
    )
    if exit_code == 0:
        sys.stdout.write(recovery_result.model_dump_json() + "\n")
    raise SystemExit(exit_code)


async def _process_due_with_database_session(
    dependencies: CliDependencies,
    current_time: datetime,
    limit: int,
) -> DueMissionProcessingResult:
    async with dependencies.session_maker() as session:
        try:
            result = await process_due_missions(
                dependencies.mission_repository_factory(session),
                dependencies.identity_repository_factory(session),
                current_time,
                limit=limit,
                provider_resolver=dependencies.provider_resolver,
            )
            await session.commit()
            return result
        except Exception:
            await session.rollback()
            raise


async def _recover_stale_with_database_session(
    dependencies: CliDependencies,
    current_time: datetime,
    claim_timeout: timedelta,
    limit: int,
) -> list[Mission]:
    async with dependencies.session_maker() as session:
        try:
            repository = dependencies.mission_repository_factory(session)
            return await repository.recover_stale_processing(
                current_time,
                claim_timeout,
                limit,
            )
        except Exception:
            await session.rollback()
            raise


async def rebuild_provider_history_command(
    *,
    dependencies: CliDependencies | None = None,
    stderr: TextIO | None = None,
) -> tuple[int, ProviderHistoryProjectionRebuildResult]:
    error_output = stderr or sys.stderr
    resolved_dependencies = dependencies or get_cli_dependencies()
    try:
        async with resolved_dependencies.session_maker() as session:
            result = await RebuildProviderHistoryProjection().execute(session)
            await session.commit()
            return 0, result
    except Exception:
        error_output.write("Infrastructure error while rebuilding provider history.\n")
        return 1, ProviderHistoryProjectionRebuildResult(0, 0, 0)


async def rebuild_mission_event_projection_command(
    *,
    dependencies: CliDependencies | None = None,
    stderr: TextIO | None = None,
) -> tuple[int, MissionEventProjectionRebuildResult]:
    error_output = stderr or sys.stderr
    resolved_dependencies = dependencies or get_cli_dependencies()
    try:
        async with resolved_dependencies.session_maker() as session:
            result = await RebuildMissionEventProjection().execute(session)
            await session.commit()
            return 0, result
    except Exception:
        error_output.write("Infrastructure error while rebuilding mission events.\n")
        return 1, MissionEventProjectionRebuildResult(0, 0)


def _resolve_dependencies(
    dependencies: CliDependencies | None,
    session_maker: async_sessionmaker[AsyncSession] | None,
) -> CliDependencies:
    resolved_dependencies = dependencies or get_cli_dependencies()
    if session_maker is None:
        return resolved_dependencies
    return replace(resolved_dependencies, session_maker=session_maker)


def _notification_webhook_bearer_token() -> str | None:
    token = settings.notification_webhook_bearer_token
    return token.get_secret_value() if token is not None else None


def _notification_webhook_signing_secret() -> str | None:
    secret = settings.notification_webhook_signing_secret
    return secret.get_secret_value() if secret is not None else None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="Purchase Agent command line tools.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    process_due_parser = subparsers.add_parser(
        "process-due",
        help="Run one processing cycle for due missions.",
    )
    process_due_parser.add_argument(
        "--limit",
        type=_parse_limit,
        default=100,
        help="Maximum number of due missions to process, from 1 to 500.",
    )

    worker_parser = subparsers.add_parser(
        "worker",
        help="Continuously process due missions until stopped.",
    )
    worker_parser.add_argument(
        "--poll-interval-seconds",
        type=_parse_poll_interval_seconds,
        default=settings.worker_poll_interval_seconds,
        help="Delay between processing cycles, greater than 0 and up to 3600.",
    )
    worker_parser.add_argument(
        "--claim-timeout-seconds",
        type=_parse_claim_timeout_seconds,
        default=settings.worker_claim_timeout_seconds,
        help="Age after which a processing claim is stale, from 1 to 86400.",
    )
    worker_parser.add_argument(
        "--limit",
        type=_parse_limit,
        default=settings.worker_batch_size,
        help="Maximum number of due missions per cycle, from 1 to 500.",
    )

    recover_stale_parser = subparsers.add_parser(
        "recover-stale",
        help="Recover stale processing missions without running them.",
    )
    recover_stale_parser.add_argument(
        "--claim-timeout-seconds",
        type=_parse_claim_timeout_seconds,
        default=900,
        help="Maximum claim age in seconds, from 1 to 86400.",
    )

    subparsers.add_parser(
        "rebuild-provider-history",
        help="Rebuild the provider history projection from Mission events.",
    )
    dispatch_notifications_parser = subparsers.add_parser(
        "dispatch-notifications",
        help="Deliver one batch of pending notification outbox messages.",
    )
    dispatch_notifications_parser.add_argument(
        "--limit",
        type=_parse_limit,
        default=100,
        help="Maximum number of notifications to deliver, from 1 to 500.",
    )
    notification_worker_parser = subparsers.add_parser(
        "notification-worker",
        help="Continuously recover and deliver notification outbox messages.",
    )
    notification_worker_parser.add_argument(
        "--poll-interval-seconds",
        type=_parse_poll_interval_seconds,
        default=settings.notification_worker_poll_interval_seconds,
    )
    notification_worker_parser.add_argument(
        "--claim-timeout-seconds",
        type=_parse_claim_timeout_seconds,
        default=settings.notification_claim_timeout_seconds,
    )
    notification_worker_parser.add_argument(
        "--limit",
        type=_parse_limit,
        default=settings.notification_worker_batch_size,
    )
    prune_notifications_parser = subparsers.add_parser(
        "prune-notifications",
        help="Delete an oldest batch of delivered notification records.",
    )
    prune_notifications_parser.add_argument(
        "--retention-days",
        type=_parse_retention_days,
        default=30,
        help="Delete delivered records older than this many days, from 1 to 3650.",
    )
    prune_notifications_parser.add_argument(
        "--limit",
        type=_parse_limit,
        default=500,
        help="Maximum number of delivered records to delete, from 1 to 500.",
    )
    prune_notifications_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List matching records without locking or deleting them.",
    )
    subparsers.add_parser(
        "rebuild-mission-events",
        help="Rebuild the Mission event projection from canonical Mission logs.",
    )
    recover_stale_parser.add_argument(
        "--limit",
        type=_parse_limit,
        default=100,
        help="Maximum number of stale missions to recover, from 1 to 500.",
    )

    return parser


def _parse_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("limit must be an integer") from exc

    if limit < 1 or limit > 500:
        raise argparse.ArgumentTypeError("limit must be between 1 and 500")
    return limit


def _parse_claim_timeout_seconds(value: str) -> int:
    try:
        claim_timeout_seconds = int(value)
    except ValueError as exc:
        message = "claim-timeout-seconds must be an integer"
        raise argparse.ArgumentTypeError(message) from exc

    if claim_timeout_seconds < 1 or claim_timeout_seconds > 86400:
        message = "claim-timeout-seconds must be between 1 and 86400"
        raise argparse.ArgumentTypeError(message)
    return claim_timeout_seconds


def _parse_poll_interval_seconds(value: str) -> float:
    try:
        interval = float(value)
    except ValueError as exc:
        message = "poll-interval-seconds must be a number"
        raise argparse.ArgumentTypeError(message) from exc
    if interval <= 0 or interval > 3600:
        message = "poll-interval-seconds must be greater than 0 and at most 3600"
        raise argparse.ArgumentTypeError(message)
    return interval


def _parse_retention_days(value: str) -> int:
    try:
        days = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "retention-days must be an integer"
        ) from exc
    if days < 1 or days > 3650:
        raise argparse.ArgumentTypeError(
            "retention-days must be between 1 and 3650"
        )
    return days


async def _run_worker_until_signal(
    poll_interval: timedelta,
    limit: int,
    claim_timeout: timedelta,
) -> int:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed_signals: list[signal.Signals] = []
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_number, stop_event.set)
        except (NotImplementedError, RuntimeError):
            continue
        installed_signals.append(signal_number)
    try:
        return await worker_command(
            poll_interval,
            limit,
            claim_timeout=claim_timeout,
            stop_event=stop_event,
        )
    finally:
        for signal_number in installed_signals:
            loop.remove_signal_handler(signal_number)


async def _run_notification_worker_until_signal(
    poll_interval: timedelta,
    limit: int,
    claim_timeout: timedelta,
) -> int:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed_signals: list[signal.Signals] = []
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_number, stop_event.set)
        except (NotImplementedError, RuntimeError):
            continue
        installed_signals.append(signal_number)
    try:
        return await notification_worker_command(
            poll_interval,
            limit,
            claim_timeout=claim_timeout,
            stop_event=stop_event,
        )
    finally:
        for signal_number in installed_signals:
            loop.remove_signal_handler(signal_number)


if __name__ == "__main__":
    main()

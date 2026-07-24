import argparse
import asyncio
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Callable, TextIO
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters import provider_registry
from app.db.session import async_session_maker
from app.domain.mission import Mission
from app.repositories.identity import IdentityRepository
from app.repositories.mission import MissionRepository
from app.repositories.sqlalchemy.identity import SqlAlchemyIdentityRepository
from app.repositories.sqlalchemy.mission import SqlAlchemyMissionRepository
from app.services.clock import utc_now
from app.services.due_mission_processor import (
    DueMissionProcessingResult,
    process_due_missions,
)
from app.services.provider_resolver import ProviderResolver
from app.services.provider_history_rebuild import (
    ProviderHistoryProjectionRebuildResult,
    RebuildProviderHistoryProjection,
)


@dataclass(frozen=True)
class CliDependencies:
    session_maker: async_sessionmaker[AsyncSession]
    mission_repository_factory: Callable[
        [AsyncSession], MissionRepository
    ] = SqlAlchemyMissionRepository
    identity_repository_factory: Callable[
        [AsyncSession], IdentityRepository
    ] = SqlAlchemyIdentityRepository
    provider_resolver: ProviderResolver = ProviderResolver(provider_registry)
    clock: Callable[[], datetime] = utc_now


class StaleMissionRecoveryResult(BaseModel):
    recovered_count: int
    recovered_mission_ids: list[UUID]


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


def main(argv: Sequence[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "process-due":
        raise SystemExit(asyncio.run(process_due_command(args.limit)))
    if args.command == "rebuild-provider-history":
        exit_code, result = asyncio.run(
            rebuild_provider_history_command()
        )
        if exit_code == 0:
            sys.stdout.write(
                "Processed missions: "
                f"{result.processed_missions}\n"
                "Processed provider events: "
                f"{result.processed_provider_events}\n"
                f"Inserted rows: {result.inserted_rows}\n"
            )
        raise SystemExit(exit_code)

    exit_code, result = asyncio.run(
        recover_stale_command(
            timedelta(seconds=args.claim_timeout_seconds),
            args.limit,
        )
    )
    if exit_code == 0:
        sys.stdout.write(result.model_dump_json() + "\n")
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


def _resolve_dependencies(
    dependencies: CliDependencies | None,
    session_maker: async_sessionmaker[AsyncSession] | None,
) -> CliDependencies:
    resolved_dependencies = dependencies or get_cli_dependencies()
    if session_maker is None:
        return resolved_dependencies
    return replace(resolved_dependencies, session_maker=session_maker)


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


if __name__ == "__main__":
    main()

import asyncio
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.mission import Mission, MissionStatus
from app.repositories.identity import IdentityRepository
from app.repositories.mission import MissionRepository
from app.services.clock import utc_now
from app.services.mission_engine import run_mission
from app.services.mission_expiration import expire_due_missions
from app.services.mission_retry import MissionRetryTrigger, retry_mission
from app.services.mission_retry_policy import (
    MissionRetryPolicy,
    default_mission_retry_policy,
)
from app.services.provider_resolver import ProviderResolver


class DueMissionProcessingResult(BaseModel):
    processed_count: int
    expired_mission_ids: list[UUID] = Field(default_factory=list)
    succeeded_mission_ids: list[UUID] = Field(default_factory=list)
    failed_mission_ids: list[UUID] = Field(default_factory=list)
    retry_scheduled_mission_ids: list[UUID] = Field(default_factory=list)
    errors: dict[UUID, str] = Field(default_factory=dict)


async def process_due_missions(
    mission_repository: MissionRepository,
    identity_repository: IdentityRepository,
    current_time: datetime,
    limit: int = 100,
    provider_resolver: ProviderResolver | None = None,
    retry_policy: MissionRetryPolicy = default_mission_retry_policy,
) -> DueMissionProcessingResult:
    expired_missions = await expire_due_missions(
        mission_repository,
        current_time,
        limit=limit,
    )
    claimed_missions = await mission_repository.claim_due(current_time, limit)
    result = DueMissionProcessingResult(
        processed_count=len(claimed_missions),
        expired_mission_ids=[
            mission.id for mission in expired_missions
        ],
    )

    for mission in claimed_missions:
        try:
            updated_mission = await run_mission(
                mission.id,
                mission_repository,
                identity_repository,
                provider_resolver,
                current_time=current_time,
                allow_processing=True,
            )
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            failed_mission = await _mark_claimed_mission_failed(
                mission,
                mission_repository,
                str(exc),
            )
            result.errors[mission.id] = str(exc)
            if retry_policy.should_retry_exception(exc):
                await _schedule_automatic_retry(
                    failed_mission,
                    mission_repository,
                    current_time,
                    retry_policy,
                    reason=type(exc).__name__,
                )
                result.retry_scheduled_mission_ids.append(mission.id)
            else:
                result.failed_mission_ids.append(mission.id)
            continue

        if updated_mission.status in {
            MissionStatus.requires_confirmation,
            MissionStatus.completed,
        }:
            result.succeeded_mission_ids.append(updated_mission.id)
        elif updated_mission.status is MissionStatus.failed:
            if retry_policy.should_retry_mission(updated_mission):
                await _schedule_automatic_retry(
                    updated_mission,
                    mission_repository,
                    current_time,
                    retry_policy,
                    reason=updated_mission.execution_log[-1].type,
                )
                result.retry_scheduled_mission_ids.append(updated_mission.id)
            else:
                await _record_terminal_failure(
                    updated_mission,
                    mission_repository,
                    current_time,
                )
                result.failed_mission_ids.append(updated_mission.id)

    return result


async def _record_terminal_failure(
    mission: Mission,
    mission_repository: MissionRepository,
    current_time: datetime,
) -> Mission:
    reason = (
        mission.execution_log[-1].type
        if mission.execution_log
        else "unknown"
    )
    mission.record_event(
        timestamp=current_time,
        event_type="mission_failed",
        message="Mission execution ended without another retry.",
        metadata={
            "reason": reason,
            "execution_attempts": mission.execution_attempts,
            "max_execution_attempts": mission.max_execution_attempts,
            "attempts_exhausted": mission.has_exhausted_attempts,
        },
    )
    return await mission_repository.update(mission)


async def _mark_claimed_mission_failed(
    mission: Mission,
    mission_repository: MissionRepository,
    message: str,
) -> Mission:
    stored_mission = await mission_repository.get(mission.id)
    failed_mission = stored_mission or mission
    failed_mission.status = MissionStatus.failed
    failed_mission.claimed_at = None
    _add_event(
        failed_mission,
        "mission_processing_failed",
        "Mission processing failed.",
        {"message": message},
    )
    return await mission_repository.update(failed_mission)


async def _schedule_automatic_retry(
    mission: Mission,
    mission_repository: MissionRepository,
    current_time: datetime,
    retry_policy: MissionRetryPolicy,
    *,
    reason: str,
) -> Mission:
    retry_at = current_time + retry_policy.delay_after_attempt(
        mission.execution_attempts
    )
    if mission.expires_at is not None:
        retry_at = min(retry_at, mission.expires_at)
    return await retry_mission(
        mission.id,
        mission_repository,
        retry_at=retry_at,
        current_time=current_time,
        trigger=MissionRetryTrigger.AUTOMATIC,
        reason=reason,
    )


def _add_event(
    mission: Mission,
    event_type: str,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    mission.record_event(
        timestamp=utc_now(),
        event_type=event_type,
        message=message,
        metadata=metadata,
    )

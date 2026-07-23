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
from app.services.provider_resolver import ProviderResolver


class DueMissionProcessingResult(BaseModel):
    processed_count: int
    succeeded_mission_ids: list[UUID] = Field(default_factory=list)
    failed_mission_ids: list[UUID] = Field(default_factory=list)
    errors: dict[UUID, str] = Field(default_factory=dict)


async def process_due_missions(
    mission_repository: MissionRepository,
    identity_repository: IdentityRepository,
    current_time: datetime,
    limit: int = 100,
    provider_resolver: ProviderResolver | None = None,
) -> DueMissionProcessingResult:
    claimed_missions = await mission_repository.claim_due(current_time, limit)
    result = DueMissionProcessingResult(processed_count=len(claimed_missions))

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
            await _mark_claimed_mission_failed(
                mission,
                mission_repository,
                str(exc),
            )
            result.failed_mission_ids.append(mission.id)
            result.errors[mission.id] = str(exc)
            continue

        if updated_mission.status in {
            MissionStatus.requires_confirmation,
            MissionStatus.completed,
        }:
            result.succeeded_mission_ids.append(updated_mission.id)
        elif updated_mission.status is MissionStatus.failed:
            result.failed_mission_ids.append(updated_mission.id)

    return result


async def _mark_claimed_mission_failed(
    mission: Mission,
    mission_repository: MissionRepository,
    message: str,
) -> None:
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
    await mission_repository.update(failed_mission)


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

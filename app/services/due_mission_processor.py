import asyncio
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.mission import MissionStatus
from app.repositories.identity import IdentityRepository
from app.repositories.mission import MissionRepository
from app.services.mission_engine import run_mission


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
) -> DueMissionProcessingResult:
    due_missions = await mission_repository.list_due(current_time, limit)
    result = DueMissionProcessingResult(processed_count=len(due_missions))

    for mission in due_missions:
        try:
            updated_mission = await run_mission(
                mission.id,
                mission_repository,
                identity_repository,
                current_time=current_time,
            )
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
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

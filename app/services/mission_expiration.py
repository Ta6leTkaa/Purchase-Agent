from datetime import datetime

from app.domain.mission import Mission, MissionStatus
from app.repositories.mission import MissionRepository
from app.services.mission_state_machine import MissionStateMachine

EXPIRABLE_STATUSES = {
    MissionStatus.created,
    MissionStatus.waiting,
    MissionStatus.paused,
}


async def expire_due_missions(
    mission_repository: MissionRepository,
    current_time: datetime,
    *,
    limit: int = 100,
) -> list[Mission]:
    _validate_arguments(current_time, limit)
    return await mission_repository.expire_due(current_time, limit)


async def expire_mission_if_due(
    mission: Mission,
    mission_repository: MissionRepository,
    current_time: datetime,
) -> bool:
    if (
        mission.status not in EXPIRABLE_STATUSES
        or mission.expires_at is None
        or mission.expires_at > current_time
    ):
        return False

    previous_status = mission.status
    MissionStateMachine().transition(mission, MissionStatus.expired)
    mission.record_event(
        timestamp=current_time,
        event_type="mission_expired",
        message="Mission expired before execution.",
        metadata={
            "expires_at": mission.expires_at.isoformat(),
            "previous_status": previous_status.value,
        },
    )
    await mission_repository.update(mission)
    return True


def _validate_arguments(current_time: datetime, limit: int) -> None:
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        raise ValueError("current_time must be timezone-aware")
    if limit < 1:
        raise ValueError("limit must be at least one")

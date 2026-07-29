from datetime import datetime
from uuid import UUID

from app.domain.mission import Mission, MissionStatus
from app.repositories.mission import MissionRepository
from app.services.clock import utc_now
from app.services.mission_errors import MissionNotFoundError
from app.services.mission_state_machine import MissionStateMachine


class MissionSchedulingNotAllowedError(Exception):
    def __init__(self, status: MissionStatus) -> None:
        self.status = status
        super().__init__(
            "Mission scheduling cannot be changed from status "
            f"'{status.value}'"
        )


class InvalidMissionScheduleError(ValueError):
    pass


async def schedule_mission(
    mission_id: UUID,
    scheduled_at: datetime,
    mission_repository: MissionRepository,
    *,
    current_time: datetime | None = None,
) -> Mission:
    now = current_time or utc_now()
    _validate_schedule_time(scheduled_at, now)
    mission = await mission_repository.get(mission_id)
    if mission is None:
        raise MissionNotFoundError
    if mission.status not in {MissionStatus.created, MissionStatus.waiting}:
        raise MissionSchedulingNotAllowedError(mission.status)
    if mission.scheduled_at == scheduled_at:
        return mission

    previous_scheduled_at = mission.scheduled_at
    mission.scheduled_at = scheduled_at
    if mission.status is MissionStatus.created:
        MissionStateMachine().transition(mission, MissionStatus.waiting)
    mission.record_event(
        timestamp=now,
        event_type="mission_scheduled",
        message="Mission scheduled.",
        metadata={
            "previous_scheduled_at": (
                previous_scheduled_at.isoformat()
                if previous_scheduled_at is not None
                else None
            ),
            "scheduled_at": scheduled_at.isoformat(),
        },
    )
    return await mission_repository.update(mission)


def _validate_schedule_time(scheduled_at: datetime, current_time: datetime) -> None:
    for value in (scheduled_at, current_time):
        if value.tzinfo is None or value.utcoffset() is None:
            raise InvalidMissionScheduleError(
                "schedule times must be timezone-aware"
            )
    if scheduled_at <= current_time:
        raise InvalidMissionScheduleError("scheduled_at must be in the future")

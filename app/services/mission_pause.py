from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from app.domain.mission import Mission, MissionStatus
from app.repositories.mission import MissionRepository
from app.services.clock import utc_now
from app.services.mission_errors import MissionNotFoundError
from app.services.mission_state_machine import MissionStateMachine


class MissionPauseNotAllowedError(Exception):
    def __init__(self, status: MissionStatus) -> None:
        self.status = status
        super().__init__(
            f"Mission cannot be paused from status '{status.value}'"
        )


class MissionResumeNotAllowedError(Exception):
    def __init__(self, status: MissionStatus) -> None:
        self.status = status
        super().__init__(
            f"Mission cannot be resumed from status '{status.value}'"
        )


async def pause_mission(
    mission_id: UUID,
    mission_repository: MissionRepository,
    *,
    clock: Callable[[], datetime] = utc_now,
) -> Mission:
    mission = await mission_repository.get(mission_id)
    if mission is None:
        raise MissionNotFoundError
    if mission.status not in {MissionStatus.created, MissionStatus.waiting}:
        raise MissionPauseNotAllowedError(mission.status)

    previous_status = mission.status
    MissionStateMachine().transition(mission, MissionStatus.paused)
    mission.record_event(
        timestamp=clock(),
        event_type="mission_paused",
        message="Mission paused.",
        metadata={"previous_status": previous_status.value},
    )
    return await mission_repository.update(mission)


async def resume_mission(
    mission_id: UUID,
    mission_repository: MissionRepository,
    *,
    clock: Callable[[], datetime] = utc_now,
) -> Mission:
    mission = await mission_repository.get(mission_id)
    if mission is None:
        raise MissionNotFoundError
    if mission.status is not MissionStatus.paused:
        raise MissionResumeNotAllowedError(mission.status)

    target = (
        MissionStatus.waiting
        if mission.scheduled_at is not None
        else MissionStatus.created
    )
    MissionStateMachine().transition(mission, target)
    mission.record_event(
        timestamp=clock(),
        event_type="mission_resumed",
        message="Mission resumed.",
        metadata={"resumed_status": target.value},
    )
    return await mission_repository.update(mission)

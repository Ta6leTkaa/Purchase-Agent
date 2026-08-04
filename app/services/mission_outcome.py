from enum import StrEnum

from pydantic import BaseModel

from app.domain.mission import Mission, MissionStatus
from app.domain.provider import ProviderOption


class MissionNextAction(StrEnum):
    RUN = "run"
    WAIT = "wait"
    RESUME = "resume"
    CONFIRM = "confirm"
    RETRY = "retry"
    NONE = "none"


class MissionOutcome(BaseModel):
    status: MissionStatus
    terminal: bool
    successful: bool
    next_action: MissionNextAction
    selected_option: ProviderOption | None
    reservation_id: str | None


def get_mission_outcome(mission: Mission) -> MissionOutcome:
    next_action = _next_action(mission)
    terminal = mission.status in _TERMINAL_STATUSES or (
        mission.status is MissionStatus.failed and mission.has_exhausted_attempts
    )
    return MissionOutcome(
        status=mission.status,
        terminal=terminal,
        successful=mission.status is MissionStatus.completed,
        next_action=next_action,
        selected_option=mission.best_option,
        reservation_id=mission.reservation_id,
    )


_TERMINAL_STATUSES = frozenset(
    {
        MissionStatus.completed,
        MissionStatus.cancelled,
        MissionStatus.expired,
    }
)


def _next_action(mission: Mission) -> MissionNextAction:
    if mission.status is MissionStatus.created:
        return MissionNextAction.RUN
    if mission.status is MissionStatus.paused:
        return MissionNextAction.RESUME
    if mission.status is MissionStatus.requires_confirmation:
        return MissionNextAction.CONFIRM
    if mission.status is MissionStatus.failed and not mission.has_exhausted_attempts:
        return MissionNextAction.RETRY
    if mission.status in {
        MissionStatus.waiting,
        MissionStatus.processing,
        MissionStatus.running,
        MissionStatus.searching,
        MissionStatus.option_found,
        MissionStatus.reserving,
    }:
        return MissionNextAction.WAIT
    return MissionNextAction.NONE

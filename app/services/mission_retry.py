from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.domain.mission import Mission, MissionStatus
from app.repositories.mission import MissionRepository
from app.services.clock import utc_now
from app.services.mission_errors import MissionNotFoundError
from app.services.mission_state_machine import MissionStateMachine


class MissionRetryNotAllowedError(Exception):
    def __init__(self, status: MissionStatus) -> None:
        self.status = status
        super().__init__(
            f"Mission cannot be retried from status '{status.value}'"
        )


class MissionAttemptsExhaustedError(Exception):
    pass


class InvalidMissionRetryTimeError(ValueError):
    pass


class MissionRetryTrigger(StrEnum):
    MANUAL = "manual"
    AUTOMATIC = "automatic"


async def retry_mission(
    mission_id: UUID,
    mission_repository: MissionRepository,
    *,
    retry_at: datetime | None = None,
    current_time: datetime | None = None,
    trigger: MissionRetryTrigger = MissionRetryTrigger.MANUAL,
    reason: str | None = None,
) -> Mission:
    now = current_time or utc_now()
    _require_aware_time(now, field_name="current_time")
    effective_retry_at = retry_at or now
    _require_aware_time(effective_retry_at, field_name="retry_at")
    if effective_retry_at < now:
        raise InvalidMissionRetryTimeError(
            "retry_at must not be in the past"
        )

    mission = await mission_repository.get(mission_id)
    if mission is None:
        raise MissionNotFoundError
    if mission.status is not MissionStatus.failed:
        raise MissionRetryNotAllowedError(mission.status)
    if mission.has_exhausted_attempts:
        raise MissionAttemptsExhaustedError(
            "Mission execution attempts are exhausted"
        )

    previous_resolved_provider_id = mission.resolved_provider_id
    previous_reservation_id = mission.reservation_id
    MissionStateMachine().retry_failed(mission, now)
    mission.scheduled_at = effective_retry_at
    mission.resolved_provider_id = None
    mission.reservation_id = None
    mission.best_option = None
    mission.record_event(
        timestamp=now,
        event_type="mission_retry_scheduled",
        message="Mission scheduled for another execution attempt.",
        metadata={
            "retry_at": effective_retry_at.isoformat(),
            "execution_attempts": mission.execution_attempts,
            "max_execution_attempts": mission.max_execution_attempts,
            "previous_resolved_provider_id": previous_resolved_provider_id,
            "previous_reservation_id": previous_reservation_id,
            "trigger": trigger.value,
            "reason": reason,
        },
    )
    return await mission_repository.update(mission)


def _require_aware_time(value: datetime, *, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidMissionRetryTimeError(
            f"{field_name} must be timezone-aware"
        )

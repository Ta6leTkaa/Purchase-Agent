from datetime import datetime

from app.domain.mission import Mission, MissionStatus


class InvalidMissionTransitionError(Exception):
    pass


class MissionStateMachine:
    _allowed_transitions: dict[MissionStatus, set[MissionStatus]] = {
        MissionStatus.created: {
            MissionStatus.waiting,
            MissionStatus.paused,
            MissionStatus.running,
            MissionStatus.cancelled,
            MissionStatus.expired,
        },
        MissionStatus.waiting: {
            MissionStatus.created,
            MissionStatus.paused,
            MissionStatus.processing,
            MissionStatus.running,
            MissionStatus.cancelled,
            MissionStatus.expired,
        },
        MissionStatus.paused: {
            MissionStatus.created,
            MissionStatus.waiting,
            MissionStatus.cancelled,
            MissionStatus.expired,
        },
        MissionStatus.processing: {
            MissionStatus.requires_confirmation,
            MissionStatus.completed,
            MissionStatus.failed,
        },
        MissionStatus.running: {
            MissionStatus.searching,
            MissionStatus.failed,
        },
        MissionStatus.searching: {
            MissionStatus.option_found,
            MissionStatus.failed,
        },
        MissionStatus.option_found: {
            MissionStatus.reserving,
            MissionStatus.completed,
            MissionStatus.failed,
        },
        MissionStatus.reserving: {
            MissionStatus.requires_confirmation,
            MissionStatus.completed,
            MissionStatus.failed,
        },
        MissionStatus.requires_confirmation: {
            MissionStatus.completed,
            MissionStatus.failed,
            MissionStatus.cancelled,
        },
        MissionStatus.completed: set(),
        MissionStatus.failed: set(),
        MissionStatus.cancelled: set(),
        MissionStatus.expired: set(),
    }

    def transition(
        self,
        mission: Mission,
        target: MissionStatus,
        current_time: datetime | None = None,
        *,
        recovery: bool = False,
        retry: bool = False,
    ) -> Mission:
        current = mission.status
        if not self.can_transition(
            current,
            target,
            recovery=recovery,
            retry=retry,
        ):
            message = (
                "Invalid mission status transition: "
                f"{current.value} -> {target.value}"
            )
            raise InvalidMissionTransitionError(message)

        if (
            current is MissionStatus.waiting
            and target is MissionStatus.processing
        ) or recovery or retry:
            _require_aware_current_time(current_time)

        mission.status = target
        if current is MissionStatus.waiting and target is MissionStatus.processing:
            mission.claimed_at = current_time
        elif current is MissionStatus.processing:
            mission.claimed_at = None
        else:
            mission.claimed_at = None
        return mission

    def retry_failed(
        self,
        mission: Mission,
        current_time: datetime,
    ) -> Mission:
        return self.transition(
            mission,
            MissionStatus.waiting,
            current_time=current_time,
            retry=True,
        )

    def recover_stale(
        self,
        mission: Mission,
        current_time: datetime,
    ) -> Mission:
        previous_claimed_at = mission.claimed_at
        target = (
            MissionStatus.failed
            if mission.has_exhausted_attempts
            else MissionStatus.waiting
        )
        self.transition(
            mission,
            target,
            current_time=current_time,
            recovery=True,
        )
        mission.record_event(
            timestamp=current_time,
            event_type="claim_recovered",
            message=(
                "Mission failed after a stale claim because execution "
                "attempts are exhausted."
                if target is MissionStatus.failed
                else "Mission recovered after a stale claim."
            ),
            metadata={
                "previous_claimed_at": previous_claimed_at.isoformat()
                if previous_claimed_at is not None
                else None,
                "attempts_exhausted": target is MissionStatus.failed,
            },
        )
        return mission

    def can_transition(
        self,
        current: MissionStatus,
        target: MissionStatus,
        *,
        recovery: bool = False,
        retry: bool = False,
    ) -> bool:
        if (
            current is MissionStatus.processing
            and target is MissionStatus.waiting
        ):
            return recovery
        if current is MissionStatus.failed and target is MissionStatus.waiting:
            return retry
        return target in self._allowed_transitions[current]


def _require_aware_current_time(current_time: datetime | None) -> None:
    if current_time is None:
        message = "current_time is required for processing transition"
        raise InvalidMissionTransitionError(message)
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        message = "current_time must be timezone-aware"
        raise InvalidMissionTransitionError(message)

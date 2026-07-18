from datetime import datetime

from app.domain.mission import Mission, MissionStatus


class InvalidMissionTransitionError(Exception):
    pass


class MissionStateMachine:
    _allowed_transitions: dict[MissionStatus, set[MissionStatus]] = {
        MissionStatus.created: {
            MissionStatus.waiting,
            MissionStatus.running,
        },
        MissionStatus.waiting: {
            MissionStatus.processing,
            MissionStatus.running,
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
        },
        MissionStatus.completed: set(),
        MissionStatus.failed: set(),
    }

    def transition(
        self,
        mission: Mission,
        target: MissionStatus,
        current_time: datetime | None = None,
    ) -> Mission:
        current = mission.status
        if not self.can_transition(current, target):
            message = (
                "Invalid mission status transition: "
                f"{current.value} -> {target.value}"
            )
            raise InvalidMissionTransitionError(message)

        if current is MissionStatus.waiting and target is MissionStatus.processing:
            if current_time is None:
                message = "current_time is required for processing transition"
                raise InvalidMissionTransitionError(message)
            if current_time.tzinfo is None or current_time.utcoffset() is None:
                message = "current_time must be timezone-aware"
                raise InvalidMissionTransitionError(message)

        mission.status = target
        if current is MissionStatus.waiting and target is MissionStatus.processing:
            mission.claimed_at = current_time
        elif current is MissionStatus.processing:
            mission.claimed_at = None
        else:
            mission.claimed_at = None
        return mission

    def can_transition(
        self,
        current: MissionStatus,
        target: MissionStatus,
    ) -> bool:
        return target in self._allowed_transitions[current]

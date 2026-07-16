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
            MissionStatus.running,
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

    def transition(self, mission: Mission, target: MissionStatus) -> Mission:
        current = mission.status
        if not self.can_transition(current, target):
            message = (
                "Invalid mission status transition: "
                f"{current.value} -> {target.value}"
            )
            raise InvalidMissionTransitionError(message)

        mission.status = target
        return mission

    def can_transition(
        self,
        current: MissionStatus,
        target: MissionStatus,
    ) -> bool:
        return target in self._allowed_transitions[current]

from app.domain.mission import MissionType


class UnsupportedMissionTypeError(Exception):
    def __init__(
        self,
        provider_id: str,
        mission_type: MissionType,
    ) -> None:
        self.provider_id = provider_id
        self.mission_type = mission_type
        super().__init__(
            "Provider "
            f"'{provider_id}' does not support mission type "
            f"'{mission_type.value}'"
        )

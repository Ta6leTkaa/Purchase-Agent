from app.domain.mission import MissionExecutionMode, MissionType


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


class ProviderOperationError(Exception):
    """Raised by an adapter for an expected, safe-to-record operation failure."""

    def __init__(
        self,
        *,
        provider_id: str,
        operation: str,
        retryable: bool = True,
    ) -> None:
        self.provider_id = provider_id
        self.operation = operation
        self.retryable = retryable
        super().__init__(
            f"Provider '{provider_id}' failed during {operation} operation"
        )


class UnsupportedExecutionModeError(Exception):
    def __init__(
        self,
        *,
        execution_mode: MissionExecutionMode,
        provider_id: str | None,
    ) -> None:
        self.execution_mode = execution_mode
        self.provider_id = provider_id
        provider_description = (
            f"Provider '{provider_id}'"
            if provider_id is not None
            else "No configured provider"
        )
        super().__init__(
            f"{provider_description} does not support execution mode "
            f"'{execution_mode.value}'"
        )

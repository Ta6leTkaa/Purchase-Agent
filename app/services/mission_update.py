from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from app.adapters.registry import ProviderRegistry
from app.domain.mission import (
    FallbackRules,
    Mission,
    MissionExecutionMode,
    MissionStatus,
)
from app.repositories.mission import MissionRepository
from app.services.clock import utc_now
from app.services.mission_errors import MissionNotFoundError
from app.services.provider_errors import UnsupportedExecutionModeError


class MissionUpdateNotAllowedError(Exception):
    def __init__(self, status: MissionStatus) -> None:
        self.status = status
        super().__init__(
            f"Mission cannot be updated from status '{status.value}'"
        )


class InvalidMissionUpdateError(ValueError):
    pass


async def update_mission(
    mission_id: UUID,
    mission_repository: MissionRepository,
    provider_registry: ProviderRegistry,
    *,
    title: str | None = None,
    fallback_rules: FallbackRules | None = None,
    execution_mode: MissionExecutionMode | None = None,
    max_execution_attempts: int | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> Mission:
    mission = await mission_repository.get(mission_id)
    if mission is None:
        raise MissionNotFoundError
    if mission.status not in {
        MissionStatus.created,
        MissionStatus.waiting,
        MissionStatus.paused,
    }:
        raise MissionUpdateNotAllowedError(mission.status)
    if (
        max_execution_attempts is not None
        and max_execution_attempts < mission.execution_attempts
    ):
        raise InvalidMissionUpdateError(
            "max_execution_attempts cannot be less than execution_attempts"
        )
    if (
        execution_mode is not None
        and execution_mode is not mission.execution_mode
        and mission.provider_id is not None
    ):
        adapter = provider_registry.get(mission.provider_id)
        if not adapter.supports_execution_mode(
            mission.mission_type,
            execution_mode,
        ):
            raise UnsupportedExecutionModeError(
                execution_mode=execution_mode,
                provider_id=adapter.provider_id,
            )

    previous: dict[str, object] = {}
    current: dict[str, object] = {}
    _apply_change(mission, "title", title, previous, current)
    _apply_change(
        mission,
        "fallback_rules",
        fallback_rules,
        previous,
        current,
    )
    _apply_change(
        mission,
        "execution_mode",
        execution_mode,
        previous,
        current,
    )
    _apply_change(
        mission,
        "max_execution_attempts",
        max_execution_attempts,
        previous,
        current,
    )
    if not current:
        return mission

    mission.record_event(
        timestamp=clock(),
        event_type="mission_updated",
        message="Mission configuration updated.",
        metadata={
            "changed_fields": sorted(current),
            "previous": _serialize_values(previous),
            "current": _serialize_values(current),
        },
    )
    return await mission_repository.update(mission)


def _apply_change(
    mission: Mission,
    field_name: str,
    value: object | None,
    previous: dict[str, object],
    current: dict[str, object],
) -> None:
    if value is None:
        return
    existing = getattr(mission, field_name)
    if existing == value:
        return
    previous[field_name] = existing
    current[field_name] = value
    setattr(mission, field_name, value)


def _serialize_values(values: dict[str, object]) -> dict[str, object]:
    serialized: dict[str, object] = {}
    for field_name, value in values.items():
        if isinstance(value, FallbackRules):
            serialized[field_name] = value.model_dump(mode="json")
        elif isinstance(value, MissionExecutionMode):
            serialized[field_name] = value.value
        else:
            serialized[field_name] = value
    return serialized

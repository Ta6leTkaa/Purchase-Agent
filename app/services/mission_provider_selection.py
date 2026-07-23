from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from app.adapters.registry import ProviderRegistry
from app.domain.mission import Mission, MissionStatus
from app.domain.provider_id import normalize_provider_id
from app.domain.provider_resolution import (
    create_provider_selection_changed_event,
)
from app.repositories.mission import MissionRepository
from app.services.clock import utc_now
from app.services.mission_errors import MissionNotFoundError
from app.services.provider_errors import UnsupportedMissionTypeError


class MissionProviderSelectionNotAllowedError(Exception):
    def __init__(
        self,
        *,
        mission_id: UUID,
        status: MissionStatus,
    ) -> None:
        self.mission_id = mission_id
        self.status = status
        super().__init__(
            "Provider selection cannot be changed from mission status "
            f"'{status.value}'"
        )


class SetMissionProvider:
    """Persist a requested provider choice without resolving or running it."""

    def __init__(
        self,
        mission_repository: MissionRepository,
        provider_registry: ProviderRegistry,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._mission_repository = mission_repository
        self._provider_registry = provider_registry
        self._clock = clock

    async def execute(
        self,
        mission_id: UUID,
        provider_id: str | None,
    ) -> Mission:
        mission = await self._mission_repository.get(mission_id)
        if mission is None:
            raise MissionNotFoundError

        if not mission.can_change_provider_selection:
            raise MissionProviderSelectionNotAllowedError(
                mission_id=mission.id,
                status=mission.status,
            )

        normalized_provider_id = normalize_provider_id(provider_id)
        if normalized_provider_id is not None:
            adapter = self._provider_registry.get(normalized_provider_id)
            if not adapter.supports(mission.mission_type):
                raise UnsupportedMissionTypeError(
                    provider_id=adapter.provider_id,
                    mission_type=mission.mission_type,
                )

        previous_provider_id = mission.provider_id
        updated_mission = mission.with_provider_selection(
            normalized_provider_id
        )
        if updated_mission is mission:
            return mission
        create_provider_selection_changed_event(
            mission=updated_mission,
            previous_provider_id=previous_provider_id,
            new_provider_id=normalized_provider_id,
            occurred_at=self._clock(),
        )
        return await self._mission_repository.update(updated_mission)

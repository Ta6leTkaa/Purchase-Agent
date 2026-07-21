from app.adapters.base import ProviderAdapter
from app.adapters.registry import ProviderRegistry
from app.domain.mission import Mission, MissionType
from app.services.provider_errors import UnsupportedMissionTypeError


class NoSupportingProviderError(LookupError):
    def __init__(self, mission_type: MissionType) -> None:
        self.mission_type = mission_type
        super().__init__(
            f"No provider supports mission type '{mission_type.value}'"
        )


class AmbiguousProviderError(LookupError):
    def __init__(
        self,
        mission_type: MissionType,
        provider_ids: tuple[str, ...],
    ) -> None:
        self.mission_type = mission_type
        self.provider_ids = provider_ids
        super().__init__(
            "Multiple providers support mission type "
            f"'{mission_type.value}': {', '.join(provider_ids)}"
        )


class ProviderResolver:
    """Resolve one adapter with deterministic, capability-based policy."""

    def __init__(self, registry: ProviderRegistry) -> None:
        self._registry = registry

    def resolve(self, mission: Mission) -> ProviderAdapter:
        if mission.provider_id is not None:
            adapter = self._registry.get(mission.provider_id)
            if not adapter.supports(mission.mission_type):
                raise UnsupportedMissionTypeError(
                    provider_id=adapter.provider_id,
                    mission_type=mission.mission_type,
                )
            return adapter

        supporting_adapters = self._registry.list_supporting(
            mission.mission_type
        )
        if not supporting_adapters:
            raise NoSupportingProviderError(mission.mission_type)
        if len(supporting_adapters) > 1:
            raise AmbiguousProviderError(
                mission_type=mission.mission_type,
                provider_ids=tuple(
                    adapter.provider_id
                    for adapter in supporting_adapters
                ),
            )
        return supporting_adapters[0]

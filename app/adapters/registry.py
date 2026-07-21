from collections.abc import Iterable
from types import MappingProxyType

from app.adapters.base import ProviderAdapter
from app.domain.mission import MissionType


class InvalidProviderIdError(ValueError):
    def __init__(self, provider_id: str) -> None:
        self.provider_id = provider_id
        super().__init__("Provider ID must be a non-empty string")


class DuplicateProviderIdError(ValueError):
    def __init__(self, provider_id: str) -> None:
        self.provider_id = provider_id
        super().__init__(f"Provider ID '{provider_id}' is already registered")


class UnknownProviderError(LookupError):
    def __init__(self, provider_id: str) -> None:
        self.provider_id = provider_id
        super().__init__(f"Unknown provider '{provider_id}'")


class ProviderRegistry:
    """Immutable catalog of configured provider adapter instances."""

    def __init__(self, adapters: Iterable[ProviderAdapter]) -> None:
        registered_adapters = tuple(adapters)
        adapters_by_id: dict[str, ProviderAdapter] = {}

        for adapter in registered_adapters:
            provider_id = adapter.provider_id
            if not isinstance(provider_id, str) or not provider_id.strip():
                raise InvalidProviderIdError(provider_id)
            if provider_id in adapters_by_id:
                raise DuplicateProviderIdError(provider_id)
            adapters_by_id[provider_id] = adapter

        self._adapters = registered_adapters
        self._adapters_by_id = MappingProxyType(adapters_by_id)

    def get(self, provider_id: str) -> ProviderAdapter:
        try:
            return self._adapters_by_id[provider_id]
        except KeyError as exc:
            raise UnknownProviderError(provider_id) from exc

    def list_all(self) -> tuple[ProviderAdapter, ...]:
        return self._adapters

    def list_supporting(
        self,
        mission_type: MissionType,
    ) -> tuple[ProviderAdapter, ...]:
        return tuple(
            adapter
            for adapter in self._adapters
            if adapter.supports(mission_type)
        )

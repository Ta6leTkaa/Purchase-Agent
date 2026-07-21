from datetime import date
from uuid import uuid4

import pytest

from app.adapters.base import ProviderAdapter
from app.adapters.mock_train import MockTrainAdapter
from app.adapters.registry import ProviderRegistry, UnknownProviderError
from app.dependencies import get_provider_registry, get_provider_resolver
from app.domain.identity import Identity
from app.domain.mission import Mission, MissionType, TrainConstraints
from app.domain.provider import ProviderOption, ReservationResult
from app.domain.provider_capability import ProviderCapability
from app.services.mission_engine import UnsupportedMissionTypeError
from app.services.provider_resolver import (
    AmbiguousProviderError,
    NoSupportingProviderError,
    ProviderResolver,
)


class FakeAdapter(ProviderAdapter):
    def __init__(self, provider_id: str, *, supports_mission: bool) -> None:
        self._provider_id = provider_id
        self._supports_mission = supports_mission
        self.search_calls = 0
        self.reserve_calls = 0

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return frozenset(
            {
                ProviderCapability(
                    mission_type=MissionType.TRAIN_TICKET,
                )
            }
        )

    def supports(self, mission_type: MissionType) -> bool:
        return self._supports_mission

    async def search_options(
        self,
        mission: Mission,
        identities: list[Identity],
    ) -> list[ProviderOption]:
        self.search_calls += 1
        return []

    async def reserve_option(
        self,
        option: ProviderOption,
        mission: Mission,
    ) -> ReservationResult:
        self.reserve_calls += 1
        raise AssertionError("Provider operations must not be called")


class SpyRegistry(ProviderRegistry):
    def __init__(self, adapters: list[ProviderAdapter]) -> None:
        super().__init__(adapters)
        self.get_calls: list[str] = []
        self.list_supporting_calls: list[MissionType] = []

    def get(self, provider_id: str) -> ProviderAdapter:
        self.get_calls.append(provider_id)
        return super().get(provider_id)

    def list_supporting(
        self,
        mission_type: MissionType,
    ) -> tuple[ProviderAdapter, ...]:
        self.list_supporting_calls.append(mission_type)
        return super().list_supporting(mission_type)


def make_mission(provider_id: str | None = None) -> Mission:
    return Mission(
        id=uuid4(),
        type=MissionType.TRAIN_TICKET,
        title="Moscow to Saint Petersburg",
        participant_ids=[uuid4()],
        provider="mock_train",
        provider_id=provider_id,
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Saint Petersburg",
            travel_date=date(2026, 8, 1),
            passengers_count=1,
        ),
    )


def test_resolve_explicit_compatible_provider_returns_registered_instance() -> None:
    adapter = MockTrainAdapter()
    mission = make_mission(provider_id="mock_train")
    before = mission.model_dump()

    resolved = ProviderResolver(ProviderRegistry([adapter])).resolve(mission)

    assert resolved is adapter
    assert mission.model_dump() == before


def test_explicit_unknown_provider_propagates_error_without_fallback() -> None:
    registry = SpyRegistry([MockTrainAdapter()])
    mission = make_mission(provider_id="missing_provider")
    before = mission.model_dump()

    with pytest.raises(UnknownProviderError) as exc_info:
        ProviderResolver(registry).resolve(mission)

    assert "missing_provider" in str(exc_info.value)
    assert registry.get_calls == ["missing_provider"]
    assert registry.list_supporting_calls == []
    assert mission.model_dump() == before


def test_explicit_incompatible_provider_does_not_fallback() -> None:
    incompatible_adapter = FakeAdapter("flight_only", supports_mission=False)
    compatible_adapter = MockTrainAdapter()
    mission = make_mission(provider_id="flight_only")
    before = mission.model_dump()

    with pytest.raises(UnsupportedMissionTypeError) as exc_info:
        ProviderResolver(
            ProviderRegistry([incompatible_adapter, compatible_adapter])
        ).resolve(mission)

    assert exc_info.value.provider_id == "flight_only"
    assert exc_info.value.mission_type is MissionType.TRAIN_TICKET
    assert incompatible_adapter.search_calls == 0
    assert incompatible_adapter.reserve_calls == 0
    assert mission.model_dump() == before


def test_resolve_without_provider_returns_only_supporting_adapter() -> None:
    adapter = MockTrainAdapter()
    registry = SpyRegistry([adapter])
    mission = make_mission()
    before = mission.model_dump()

    resolved = ProviderResolver(registry).resolve(mission)

    assert resolved is adapter
    assert registry.get_calls == []
    assert registry.list_supporting_calls == [MissionType.TRAIN_TICKET]
    assert mission.provider_id is None
    assert mission.model_dump() == before


def test_resolve_without_supporting_adapter_raises_typed_error() -> None:
    mission = make_mission()

    with pytest.raises(NoSupportingProviderError) as exc_info:
        ProviderResolver(ProviderRegistry([])).resolve(mission)

    assert exc_info.value.mission_type is MissionType.TRAIN_TICKET
    assert "train_ticket" in str(exc_info.value)


def test_resolve_with_multiple_supporting_adapters_is_ambiguous() -> None:
    first_adapter = FakeAdapter("provider_b", supports_mission=True)
    second_adapter = FakeAdapter("provider_a", supports_mission=True)
    mission = make_mission()

    with pytest.raises(AmbiguousProviderError) as exc_info:
        ProviderResolver(
            ProviderRegistry([first_adapter, second_adapter])
        ).resolve(mission)

    assert exc_info.value.mission_type is MissionType.TRAIN_TICKET
    assert exc_info.value.provider_ids == ("provider_b", "provider_a")
    assert isinstance(exc_info.value.provider_ids, tuple)


def test_provider_resolver_dependency_uses_production_registry() -> None:
    registry = get_provider_registry()
    resolver = get_provider_resolver()
    mission = make_mission()

    assert resolver is get_provider_resolver()
    assert resolver.resolve(mission) is registry.get("mock_train")

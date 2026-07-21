import pytest

from app.adapters import (
    DuplicateProviderIdError,
    InvalidProviderIdError,
    ProviderRegistry,
    UnknownProviderError,
)
from app.adapters.base import ProviderAdapter
from app.adapters.mock_train import MockTrainAdapter
from app.dependencies import get_provider_registry
from app.domain.identity import Identity
from app.domain.mission import Mission, MissionType
from app.domain.provider import ProviderOption, ReservationResult
from app.domain.provider_capability import ProviderCapability


class FakeAdapter(ProviderAdapter):
    def __init__(
        self,
        provider_id: str,
        *,
        supports_mission_type: bool = True,
    ) -> None:
        self._provider_id = provider_id
        self._supports_mission_type = supports_mission_type
        self.supports_calls: list[MissionType] = []

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
        self.supports_calls.append(mission_type)
        return self._supports_mission_type

    async def search_options(
        self,
        mission: Mission,
        identities: list[Identity],
    ) -> list[ProviderOption]:
        return []

    async def reserve_option(
        self,
        option: ProviderOption,
        mission: Mission,
    ) -> ReservationResult:
        raise NotImplementedError


def test_registry_materializes_list_tuple_and_generator() -> None:
    first = FakeAdapter("first")
    second = FakeAdapter("second")

    assert ProviderRegistry([first]).list_all() == (first,)
    assert ProviderRegistry((first, second)).list_all() == (first, second)
    assert ProviderRegistry(adapter for adapter in [first, second]).list_all() == (
        first,
        second,
    )


def test_empty_registry_is_allowed() -> None:
    assert ProviderRegistry([]).list_all() == ()


def test_list_all_is_ordered_immutable_and_isolated_from_input() -> None:
    first = FakeAdapter("first")
    second = FakeAdapter("second")
    adapters = [first]
    registry = ProviderRegistry(adapters)
    adapters.append(second)

    registered = registry.list_all()

    assert isinstance(registered, tuple)
    assert registered == (first,)
    assert registered + (second,) == (first, second)
    assert registry.list_all() == (first,)


def test_get_returns_original_instance_by_exact_provider_id() -> None:
    adapter = FakeAdapter("mock_train")
    registry = ProviderRegistry([adapter])

    assert registry.get("mock_train") is adapter
    with pytest.raises(UnknownProviderError) as exc_info:
        registry.get("Mock_Train")

    assert exc_info.value.provider_id == "Mock_Train"
    assert "Mock_Train" in str(exc_info.value)


def test_duplicate_provider_id_is_rejected_during_registry_creation() -> None:
    first = FakeAdapter("duplicate")
    second = FakeAdapter("duplicate")

    with pytest.raises(DuplicateProviderIdError) as exc_info:
        ProviderRegistry([first, second])

    assert exc_info.value.provider_id == "duplicate"


@pytest.mark.parametrize("provider_id", ["", "   "])
def test_invalid_provider_ids_are_rejected(provider_id: str) -> None:
    with pytest.raises(InvalidProviderIdError) as exc_info:
        ProviderRegistry([FakeAdapter(provider_id)])

    assert exc_info.value.provider_id == provider_id


def test_list_supporting_uses_adapter_supports_and_registration_order() -> None:
    first = FakeAdapter("first", supports_mission_type=True)
    second = FakeAdapter("second", supports_mission_type=False)
    third = FakeAdapter("third", supports_mission_type=True)
    registry = ProviderRegistry([first, second, third])

    supporting = registry.list_supporting(MissionType.TRAIN_TICKET)

    assert isinstance(supporting, tuple)
    assert supporting == (first, third)
    assert first.supports_calls == [MissionType.TRAIN_TICKET]
    assert second.supports_calls == [MissionType.TRAIN_TICKET]
    assert third.supports_calls == [MissionType.TRAIN_TICKET]


def test_list_supporting_returns_empty_tuple_without_matches() -> None:
    registry = ProviderRegistry(
        [FakeAdapter("first", supports_mission_type=False)]
    )

    assert registry.list_supporting(MissionType.TRAIN_TICKET) == ()


def test_provider_registry_dependency_returns_mock_train_singleton() -> None:
    registry = get_provider_registry()

    assert registry is get_provider_registry()
    assert isinstance(registry, ProviderRegistry)
    assert len(registry.list_all()) == 1
    adapter = registry.get("mock_train")
    assert isinstance(adapter, MockTrainAdapter)
    assert adapter.supports(MissionType.TRAIN_TICKET) is True

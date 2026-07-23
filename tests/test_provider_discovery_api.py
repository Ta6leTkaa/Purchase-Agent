import pytest
from fastapi.testclient import TestClient

from app.adapters.base import ProviderAdapter
from app.adapters.registry import ProviderRegistry
from app.api.routes.providers import provider_to_response
from app.dependencies import get_provider_registry
from app.domain.identity import Identity
from app.domain.mission import Mission, MissionType
from app.domain.provider import ProviderOption, ReservationResult
from app.domain.provider_capability import ProviderCapability
from app.main import app


class DiscoveryAdapter(ProviderAdapter):
    def __init__(
        self,
        provider_id: str,
        *,
        supports_mission_type: bool = True,
    ) -> None:
        self._provider_id = provider_id
        self._supports_mission_type = supports_mission_type
        self.supports_calls: list[MissionType] = []
        self.provider_operations: list[str] = []

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
        self.provider_operations.append("search_options")
        return []

    async def reserve_option(
        self,
        option: ProviderOption,
        mission: Mission,
    ) -> ReservationResult:
        self.provider_operations.append("reserve_option")
        raise NotImplementedError


@pytest.fixture(autouse=True)
def clear_dependency_overrides() -> None:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def configure_registry(registry: ProviderRegistry) -> TestClient:
    app.dependency_overrides[get_provider_registry] = lambda: registry
    return TestClient(app)


def test_provider_response_is_public_and_deterministic() -> None:
    adapter = DiscoveryAdapter("mock_train")

    response = provider_to_response(adapter)

    assert response.provider_id == "mock_train"
    assert response.mission_types == (MissionType.TRAIN_TICKET,)
    assert adapter.provider_operations == []


def test_list_providers_preserves_registry_order_without_operations() -> None:
    provider_b = DiscoveryAdapter("provider_b")
    provider_a = DiscoveryAdapter("provider_a")
    client = configure_registry(ProviderRegistry([provider_b, provider_a]))

    response = client.get("/providers")

    assert response.status_code == 200
    assert response.json() == {
        "providers": [
            {
                "provider_id": "provider_b",
                "mission_types": ["train_ticket"],
            },
            {
                "provider_id": "provider_a",
                "mission_types": ["train_ticket"],
            },
        ]
    }
    assert provider_b.supports_calls == []
    assert provider_a.supports_calls == []
    assert provider_b.provider_operations == []
    assert provider_a.provider_operations == []


def test_list_supporting_providers_uses_registry_filtering() -> None:
    supporting_first = DiscoveryAdapter("provider_a")
    unsupported = DiscoveryAdapter("provider_b", supports_mission_type=False)
    supporting_last = DiscoveryAdapter("provider_c")
    client = configure_registry(
        ProviderRegistry([supporting_first, unsupported, supporting_last])
    )

    response = client.get("/providers/supporting/train_ticket")

    assert response.status_code == 200
    assert response.json() == {
        "mission_type": "train_ticket",
        "providers": [
            {
                "provider_id": "provider_a",
                "mission_types": ["train_ticket"],
            },
            {
                "provider_id": "provider_c",
                "mission_types": ["train_ticket"],
            },
        ],
    }
    assert supporting_first.supports_calls == [MissionType.TRAIN_TICKET]
    assert unsupported.supports_calls == [MissionType.TRAIN_TICKET]
    assert supporting_last.supports_calls == [MissionType.TRAIN_TICKET]
    assert supporting_first.provider_operations == []
    assert unsupported.provider_operations == []
    assert supporting_last.provider_operations == []


def test_supporting_providers_returns_empty_result_for_empty_registry() -> None:
    client = configure_registry(ProviderRegistry([]))

    response = client.get("/providers/supporting/train_ticket")

    assert response.status_code == 200
    assert response.json() == {
        "mission_type": "train_ticket",
        "providers": [],
    }


def test_supporting_providers_returns_empty_result_without_matches() -> None:
    unsupported = DiscoveryAdapter("provider_b", supports_mission_type=False)
    client = configure_registry(ProviderRegistry([unsupported]))

    response = client.get("/providers/supporting/train_ticket")

    assert response.status_code == 200
    assert response.json() == {
        "mission_type": "train_ticket",
        "providers": [],
    }
    assert unsupported.supports_calls == [MissionType.TRAIN_TICKET]
    assert unsupported.provider_operations == []


def test_list_providers_returns_empty_result_for_empty_registry() -> None:
    client = configure_registry(ProviderRegistry([]))

    response = client.get("/providers")

    assert response.status_code == 200
    assert response.json() == {"providers": []}


def test_invalid_mission_type_is_rejected_without_registry_access() -> None:
    adapter = DiscoveryAdapter("mock_train")
    client = configure_registry(ProviderRegistry([adapter]))

    response = client.get("/providers/supporting/not-a-mission-type")

    assert response.status_code == 422
    assert adapter.supports_calls == []
    assert adapter.provider_operations == []

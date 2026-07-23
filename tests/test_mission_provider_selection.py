import asyncio
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.adapters.base import ProviderAdapter
from app.adapters.registry import ProviderRegistry, UnknownProviderError
from app.dependencies import get_provider_registry, mission_repository
from app.domain.identity import Identity
from app.domain.mission import (
    Mission,
    MissionStatus,
    MissionType,
    TrainConstraints,
)
from app.domain.provider import ProviderOption, ReservationResult
from app.domain.provider_capability import ProviderCapability
from app.main import app
from app.services.mission_errors import MissionNotFoundError
from app.services.mission_provider_selection import (
    MissionProviderSelectionNotAllowedError,
    SetMissionProvider,
)
from app.services.provider_errors import UnsupportedMissionTypeError
from app.storage.memory import InMemoryMissionRepository


class SelectionAdapter(ProviderAdapter):
    def __init__(self, provider_id: str, *, supported: bool = True) -> None:
        self._provider_id = provider_id
        self._supported = supported
        self.supports_calls: list[MissionType] = []
        self.operations: list[str] = []

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
        return self._supported

    async def search_options(
        self,
        mission: Mission,
        identities: list[Identity],
    ) -> list[ProviderOption]:
        self.operations.append("search_options")
        return []

    async def reserve_option(
        self,
        option: ProviderOption,
        mission: Mission,
    ) -> ReservationResult:
        self.operations.append("reserve_option")
        raise NotImplementedError


class SpyRegistry(ProviderRegistry):
    def __init__(self, adapters: list[ProviderAdapter]) -> None:
        super().__init__(adapters)
        self.get_calls: list[str] = []

    def get(self, provider_id: str) -> ProviderAdapter:
        self.get_calls.append(provider_id)
        return super().get(provider_id)


class SpyMissionRepository(InMemoryMissionRepository):
    def __init__(self) -> None:
        super().__init__()
        self.update_calls = 0

    async def update(self, mission: Mission) -> Mission:
        self.update_calls += 1
        return await super().update(mission)


def make_mission(
    *,
    status: MissionStatus = MissionStatus.created,
    provider_id: str | None = None,
    resolved_provider_id: str | None = None,
) -> Mission:
    return Mission(
        id=uuid4(),
        type=MissionType.TRAIN_TICKET,
        title="Moscow to Saint Petersburg",
        status=status,
        participant_ids=[uuid4()],
        provider="mock_train",
        provider_id=provider_id,
        resolved_provider_id=resolved_provider_id,
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Saint Petersburg",
            travel_date=date(2026, 8, 1),
            passengers_count=1,
        ),
    )


def test_mission_provider_selection_normalizes_and_clears_resolution() -> None:
    mission = make_mission(
        provider_id="provider_a",
        resolved_provider_id="provider_a",
    )

    updated = mission.with_provider_selection("  provider_b  ")

    assert updated.provider_id == "provider_b"
    assert updated.resolved_provider_id is None
    assert updated.status is mission.status
    assert updated.payload == mission.payload
    assert updated.execution_attempts == mission.execution_attempts


def test_mission_provider_selection_clear_and_idempotency() -> None:
    mission = make_mission(
        provider_id="provider_a",
        resolved_provider_id="provider_a",
    )

    cleared = mission.with_provider_selection(None)
    unchanged = mission.with_provider_selection("provider_a")

    assert cleared.provider_id is None
    assert cleared.resolved_provider_id is None
    assert unchanged is mission
    assert unchanged.resolved_provider_id == "provider_a"


@pytest.mark.parametrize("provider_id", ["", "   "])
def test_mission_provider_selection_rejects_empty_provider_id(
    provider_id: str,
) -> None:
    with pytest.raises(ValueError):
        make_mission().with_provider_selection(provider_id)


@pytest.mark.parametrize(
    ("status", "allowed"),
    [
        (MissionStatus.created, True),
        (MissionStatus.waiting, True),
        (MissionStatus.processing, False),
        (MissionStatus.running, False),
        (MissionStatus.searching, False),
        (MissionStatus.option_found, False),
        (MissionStatus.reserving, False),
        (MissionStatus.requires_confirmation, False),
        (MissionStatus.completed, False),
        (MissionStatus.failed, False),
    ],
)
def test_mission_provider_selection_state_policy(
    status: MissionStatus,
    allowed: bool,
) -> None:
    if status is MissionStatus.processing:
        mission = make_mission().model_copy(
            update={
                "status": status,
                "claimed_at": datetime.now(timezone.utc),
            }
        )
    else:
        mission = make_mission(status=status)

    assert mission.can_change_provider_selection is allowed


def test_set_mission_provider_updates_only_selection() -> None:
    async def scenario() -> None:
        repository = SpyMissionRepository()
        adapter = SelectionAdapter("provider_b")
        registry = SpyRegistry([adapter])
        mission = make_mission(resolved_provider_id="provider_a")
        await repository.create(mission)

        updated = await SetMissionProvider(repository, registry).execute(
            mission.id,
            "provider_b",
        )

        assert updated.provider_id == "provider_b"
        assert updated.resolved_provider_id is None
        assert updated.status is MissionStatus.created
        assert updated.execution_attempts == 0
        assert updated.execution_log == []
        assert repository.update_calls == 1
        assert registry.get_calls == ["provider_b"]
        assert adapter.supports_calls == [MissionType.TRAIN_TICKET]
        assert adapter.operations == []

    asyncio.run(scenario())


def test_set_mission_provider_clear_and_idempotent_updates() -> None:
    async def scenario() -> None:
        repository = SpyMissionRepository()
        registry = SpyRegistry([SelectionAdapter("provider_a")])
        mission = make_mission(
            provider_id="provider_a",
            resolved_provider_id="provider_a",
        )
        await repository.create(mission)
        use_case = SetMissionProvider(repository, registry)

        unchanged = await use_case.execute(mission.id, "provider_a")
        cleared = await use_case.execute(mission.id, None)

        assert unchanged.resolved_provider_id == "provider_a"
        assert cleared.provider_id is None
        assert cleared.resolved_provider_id is None
        assert registry.get_calls == ["provider_a"]
        assert repository.update_calls == 1

    asyncio.run(scenario())


def test_set_mission_provider_validates_state_before_registry() -> None:
    async def scenario() -> None:
        repository = SpyMissionRepository()
        registry = SpyRegistry([SelectionAdapter("provider_a")])
        mission = make_mission(status=MissionStatus.running)
        await repository.create(mission)

        with pytest.raises(MissionProviderSelectionNotAllowedError):
            await SetMissionProvider(repository, registry).execute(
                mission.id,
                "missing_provider",
            )

        assert registry.get_calls == []
        assert repository.update_calls == 0
        assert mission.provider_id is None

    asyncio.run(scenario())


def test_set_mission_provider_rejects_unknown_or_unsupported_adapter() -> None:
    async def scenario() -> None:
        repository = SpyMissionRepository()
        unsupported = SelectionAdapter("provider_b", supported=False)
        registry = SpyRegistry([unsupported])
        mission = make_mission(
            provider_id="provider_a",
            resolved_provider_id="provider_a",
        )
        await repository.create(mission)
        use_case = SetMissionProvider(repository, registry)

        with pytest.raises(UnknownProviderError):
            await use_case.execute(mission.id, "missing_provider")
        with pytest.raises(UnsupportedMissionTypeError):
            await use_case.execute(mission.id, "provider_b")

        assert repository.update_calls == 0
        assert mission.provider_id == "provider_a"
        assert mission.resolved_provider_id == "provider_a"
        assert unsupported.operations == []

    asyncio.run(scenario())


def test_set_mission_provider_reports_missing_mission_before_registry() -> None:
    async def scenario() -> None:
        repository = SpyMissionRepository()
        registry = SpyRegistry([SelectionAdapter("provider_a")])

        with pytest.raises(MissionNotFoundError):
            await SetMissionProvider(repository, registry).execute(
                uuid4(),
                "missing_provider",
            )

        assert registry.get_calls == []
        assert repository.update_calls == 0

    asyncio.run(scenario())


@pytest.fixture(autouse=True)
def clear_api_state() -> None:
    asyncio.run(mission_repository.clear())
    app.dependency_overrides.clear()
    yield
    asyncio.run(mission_repository.clear())
    app.dependency_overrides.clear()


def save_api_mission(mission: Mission) -> None:
    asyncio.run(mission_repository.create(mission))


def test_api_sets_and_clears_provider_selection() -> None:
    mission = make_mission(resolved_provider_id="old_provider")
    save_api_mission(mission)
    client = TestClient(app)

    response = client.put(
        f"/missions/{mission.id}/provider",
        json={"provider_id": "  mock_train  "},
    )

    assert response.status_code == 200
    assert response.json()["provider_id"] == "mock_train"
    assert response.json()["resolved_provider_id"] is None
    assert response.json()["status"] == "created"
    assert response.json()["execution_attempts"] == 0
    assert response.json()["execution_log"] == []

    clear_response = client.put(
        f"/missions/{mission.id}/provider",
        json={"provider_id": None},
    )

    assert clear_response.status_code == 200
    assert clear_response.json()["provider_id"] is None
    assert clear_response.json()["resolved_provider_id"] is None


def test_api_rejects_invalid_selection_requests() -> None:
    mission = make_mission()
    save_api_mission(mission)
    client = TestClient(app)

    assert client.put(f"/missions/{mission.id}/provider", json={}).status_code == 422
    assert client.put(
        f"/missions/{mission.id}/provider",
        json={"provider_id": "", "resolved_provider_id": "mock_train"},
    ).status_code == 422


def test_api_maps_unknown_provider_and_preserves_mission() -> None:
    mission = make_mission(resolved_provider_id="mock_train")
    save_api_mission(mission)
    client = TestClient(app)

    response = client.put(
        f"/missions/{mission.id}/provider",
        json={"provider_id": "missing_provider"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unknown_provider"
    stored = asyncio.run(mission_repository.get(mission.id))
    assert stored is not None
    assert stored.provider_id is None
    assert stored.resolved_provider_id == "mock_train"
    assert stored.execution_log == []


def test_api_maps_incompatible_provider_without_mutating_mission() -> None:
    mission = make_mission(
        provider_id="provider_a",
        resolved_provider_id="provider_a",
    )
    unsupported = SelectionAdapter("provider_b", supported=False)
    app.dependency_overrides[get_provider_registry] = lambda: SpyRegistry(
        [unsupported]
    )
    save_api_mission(mission)
    client = TestClient(app)

    response = client.put(
        f"/missions/{mission.id}/provider",
        json={"provider_id": "provider_b"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unsupported_mission_type"
    stored = asyncio.run(mission_repository.get(mission.id))
    assert stored is not None
    assert stored.provider_id == "provider_a"
    assert stored.resolved_provider_id == "provider_a"
    assert stored.execution_log == []
    assert unsupported.operations == []


def test_api_rejects_forbidden_state_and_unknown_mission() -> None:
    mission = make_mission(status=MissionStatus.running)
    save_api_mission(mission)
    client = TestClient(app)

    response = client.put(
        f"/missions/{mission.id}/provider",
        json={"provider_id": "missing_provider"},
    )
    missing_response = client.put(
        f"/missions/{uuid4()}/provider",
        json={"provider_id": "missing_provider"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == (
        "mission_provider_selection_not_allowed"
    )
    assert missing_response.status_code == 404

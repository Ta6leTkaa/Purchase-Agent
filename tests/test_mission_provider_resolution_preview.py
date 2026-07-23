import asyncio
from datetime import date
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.adapters.base import ProviderAdapter
from app.adapters.registry import ProviderRegistry
from app.dependencies import get_provider_resolver, mission_repository
from app.domain.identity import Identity
from app.domain.mission import Mission, MissionStatus, MissionType, TrainConstraints
from app.domain.provider import ProviderOption, ReservationResult
from app.domain.provider_capability import ProviderCapability
from app.domain.provider_resolution import (
    ProviderResolutionFailureReason,
    ProviderResolutionPreviewOutcome,
    ProviderSelectionMode,
)
from app.main import app
from app.services.mission_errors import MissionNotFoundError
from app.services.provider_resolution_preview import (
    PreviewMissionProviderResolution,
    ProviderResolutionPreview,
)
from app.services.provider_resolver import ProviderResolver
from app.storage.memory import InMemoryMissionRepository


class PreviewAdapter(ProviderAdapter):
    def __init__(self, provider_id: str, *, supports_mission_type: bool = True) -> None:
        self._provider_id = provider_id
        self._supports_mission_type = supports_mission_type
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
        return self._supports_mission_type

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


def make_mission(
    *,
    provider_id: str | None = None,
    resolved_provider_id: str | None = None,
    status: MissionStatus = MissionStatus.created,
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


def execute_preview(
    mission: Mission,
    adapters: list[ProviderAdapter],
) -> ProviderResolutionPreview:
    async def scenario() -> ProviderResolutionPreview:
        repository = InMemoryMissionRepository()
        await repository.create(mission)
        return await PreviewMissionProviderResolution(
            repository,
            ProviderResolver(ProviderRegistry(adapters)),
        ).execute(mission.id)

    return asyncio.run(scenario())


def test_preview_resolves_automatic_selection_without_mutating_mission() -> None:
    mission = make_mission(resolved_provider_id="previous_provider")
    before = mission.model_dump(mode="json")
    adapter = PreviewAdapter("provider_a")

    preview = execute_preview(mission, [adapter])

    assert preview.outcome is ProviderResolutionPreviewOutcome.resolved
    assert preview.selection_mode is ProviderSelectionMode.automatic
    assert preview.requested_provider_id is None
    assert preview.resolved_provider_id == "provider_a"
    assert preview.candidate_provider_ids == ("provider_a",)
    assert preview.failure_reason is None
    assert mission.model_dump(mode="json") == before
    assert adapter.operations == []


@pytest.mark.parametrize(
    ("provider_id", "adapters", "outcome", "reason", "candidates"),
    [
        (
            "missing_provider",
            [],
            ProviderResolutionPreviewOutcome.unknown_provider,
            ProviderResolutionFailureReason.unknown_provider,
            (),
        ),
        (
            "unsupported_provider",
            [PreviewAdapter("unsupported_provider", supports_mission_type=False)],
            ProviderResolutionPreviewOutcome.unsupported_mission_type,
            ProviderResolutionFailureReason.unsupported_mission_type,
            (),
        ),
        (
            None,
            [],
            ProviderResolutionPreviewOutcome.no_supporting_provider,
            ProviderResolutionFailureReason.no_supporting_provider,
            (),
        ),
        (
            None,
            [PreviewAdapter("provider_b"), PreviewAdapter("provider_a")],
            ProviderResolutionPreviewOutcome.ambiguous_provider,
            ProviderResolutionFailureReason.ambiguous_provider,
            ("provider_b", "provider_a"),
        ),
    ],
)
def test_preview_maps_all_resolution_failures(
    provider_id: str | None,
    adapters: list[ProviderAdapter],
    outcome: ProviderResolutionPreviewOutcome,
    reason: ProviderResolutionFailureReason,
    candidates: tuple[str, ...],
) -> None:
    mission = make_mission(provider_id=provider_id)
    before = mission.model_dump(mode="json")

    preview = execute_preview(mission, adapters)

    assert preview.outcome is outcome
    assert preview.failure_reason is reason
    assert preview.resolved_provider_id is None
    assert preview.candidate_provider_ids == candidates
    assert mission.model_dump(mode="json") == before
    assert all(adapter.operations == [] for adapter in adapters)


def test_preview_works_for_terminal_mission_status() -> None:
    mission = make_mission(status=MissionStatus.completed)

    preview = execute_preview(mission, [PreviewAdapter("provider_a")])

    assert preview.outcome is ProviderResolutionPreviewOutcome.resolved


def test_preview_reports_missing_mission() -> None:
    async def scenario() -> None:
        with pytest.raises(MissionNotFoundError):
            await PreviewMissionProviderResolution(
                InMemoryMissionRepository(),
                ProviderResolver(ProviderRegistry([])),
            ).execute(uuid4())

    asyncio.run(scenario())


def test_preview_model_rejects_invalid_semantics() -> None:
    with pytest.raises(ValidationError):
        ProviderResolutionPreview(
            mission_id=uuid4(),
            mission_type=MissionType.TRAIN_TICKET,
            selection_mode=ProviderSelectionMode.automatic,
            requested_provider_id=None,
            outcome=ProviderResolutionPreviewOutcome.resolved,
            resolved_provider_id="provider_a",
            candidate_provider_ids=(),
            failure_reason=None,
        )


@pytest.fixture(autouse=True)
def clear_api_state() -> None:
    asyncio.run(mission_repository.clear())
    app.dependency_overrides.clear()
    yield
    asyncio.run(mission_repository.clear())
    app.dependency_overrides.clear()


def test_preview_api_returns_negative_outcome_with_200_without_events() -> None:
    mission = make_mission(provider_id="missing_provider")
    asyncio.run(mission_repository.create(mission))
    client = TestClient(app)

    response = client.get(f"/missions/{mission.id}/provider-resolution")

    assert response.status_code == 200
    assert response.json() == {
        "mission_id": str(mission.id),
        "mission_type": "train_ticket",
        "selection_mode": "explicit",
        "requested_provider_id": "missing_provider",
        "outcome": "unknown_provider",
        "resolved_provider_id": None,
        "candidate_provider_ids": [],
        "failure_reason": "unknown_provider",
    }
    stored = asyncio.run(mission_repository.get(mission.id))
    assert stored is not None
    assert stored.execution_log == []
    assert stored.resolved_provider_id is None


def test_preview_api_uses_overridden_shared_resolver() -> None:
    mission = make_mission(resolved_provider_id="previous_provider")
    adapter = PreviewAdapter("provider_b")
    app.dependency_overrides[get_provider_resolver] = lambda: ProviderResolver(
        ProviderRegistry([adapter])
    )
    asyncio.run(mission_repository.create(mission))
    client = TestClient(app)

    response = client.get(f"/missions/{mission.id}/provider-resolution")

    assert response.status_code == 200
    assert response.json()["resolved_provider_id"] == "provider_b"
    stored = asyncio.run(mission_repository.get(mission.id))
    assert stored is not None
    assert stored.resolved_provider_id == "previous_provider"
    assert stored.execution_log == []
    assert adapter.operations == []


def test_preview_api_returns_404_for_missing_mission() -> None:
    client = TestClient(app)

    response = client.get(f"/missions/{uuid4()}/provider-resolution")

    assert response.status_code == 404

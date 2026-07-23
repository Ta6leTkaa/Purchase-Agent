import asyncio
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.dependencies import mission_repository
from app.domain.execution import ExecutionEvent
from app.domain.mission import Mission, MissionType, TrainConstraints
from app.domain.provider_resolution import (
    ProviderResolutionSnapshot,
    ProviderSelectionChangedEventPayload,
    ProviderResolutionFailedEventPayload,
    ProviderResolutionFailureReason,
    ProviderResolvedEventPayload,
    ProviderSelectionMode,
)
from app.main import app
from app.services.mission_errors import MissionNotFoundError
from app.services.provider_resolution_history import (
    GetMissionProviderResolutionHistory,
    ProviderHistoryEventType,
    provider_event_to_history_item,
)
from app.storage.memory import InMemoryMissionRepository

CURRENT_TIME = datetime(2026, 7, 23, 10, 0, tzinfo=timezone.utc)


def make_mission(
    execution_log: list[ExecutionEvent] | None = None,
) -> Mission:
    return Mission(
        id=uuid4(),
        type=MissionType.TRAIN_TICKET,
        title="Moscow to Saint Petersburg",
        participant_ids=[uuid4()],
        provider="mock_train",
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Saint Petersburg",
            travel_date=date(2026, 8, 1),
            passengers_count=1,
        ),
        execution_log=execution_log or [],
    )


def make_provider_events() -> list[ExecutionEvent]:
    failure_payload = ProviderResolutionFailedEventPayload(
        reason=ProviderResolutionFailureReason.ambiguous_provider,
        mission_type=MissionType.TRAIN_TICKET,
        candidate_provider_ids=("provider_a", "provider_b"),
    )
    selection_payload = ProviderSelectionChangedEventPayload(
        previous_provider_id=None,
        new_provider_id="provider_b",
        previous_selection_mode=ProviderSelectionMode.automatic,
        new_selection_mode=ProviderSelectionMode.explicit,
    )
    snapshot = ProviderResolutionSnapshot(
        selection_mode=ProviderSelectionMode.explicit,
        requested_provider_id="provider_b",
        resolved_provider_id="provider_b",
        candidate_provider_ids=("provider_b",),
        mission_type=MissionType.TRAIN_TICKET,
    )
    resolved_payload = ProviderResolvedEventPayload(
        provider_id="provider_b",
        mission_type=MissionType.TRAIN_TICKET,
        selection_mode=ProviderSelectionMode.explicit,
        snapshot=snapshot,
    )
    return [
        ExecutionEvent(
            timestamp=CURRENT_TIME + timedelta(minutes=4),
            type="mission_completed",
            message="Mission completed.",
        ),
        ExecutionEvent(
            timestamp=CURRENT_TIME,
            type="provider_resolution_failed",
            message="Provider resolution failed.",
            metadata=failure_payload.model_dump(mode="json"),
        ),
        ExecutionEvent(
            timestamp=CURRENT_TIME + timedelta(minutes=2),
            type="provider_resolved",
            message="Provider resolved for mission execution.",
            metadata=resolved_payload.model_dump(mode="json"),
        ),
        ExecutionEvent(
            timestamp=CURRENT_TIME + timedelta(minutes=1),
            type="provider_selection_changed",
            message="Mission provider selection changed.",
            metadata=selection_payload.model_dump(mode="json"),
        ),
        ExecutionEvent(
            timestamp=CURRENT_TIME + timedelta(minutes=3),
            type="mission_started",
            message="Mission started.",
        ),
    ]


def test_history_filters_provider_events_and_orders_them_chronologically() -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()
        mission = make_mission(make_provider_events())
        await repository.create(mission)

        history = await GetMissionProviderResolutionHistory(repository).execute(
            mission.id
        )

        assert history.mission_id == mission.id
        assert [item.event_type for item in history.items] == [
            ProviderHistoryEventType.provider_resolution_failed,
            ProviderHistoryEventType.provider_selection_changed,
            ProviderHistoryEventType.provider_resolved,
        ]
        assert history.items[-1].payload.model_dump(mode="json")["snapshot"] == {
            "selection_mode": "explicit",
            "requested_provider_id": "provider_b",
            "resolved_provider_id": "provider_b",
            "candidate_provider_ids": ["provider_b"],
            "mission_type": "train_ticket",
        }
        assert mission.execution_log == make_provider_events()

    asyncio.run(scenario())


def test_history_returns_empty_items_for_mission_without_provider_events() -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()
        mission = make_mission()
        await repository.create(mission)

        history = await GetMissionProviderResolutionHistory(repository).execute(
            mission.id
        )

        assert history.mission_id == mission.id
        assert history.items == ()

    asyncio.run(scenario())


def test_history_reports_missing_mission() -> None:
    async def scenario() -> None:
        with pytest.raises(MissionNotFoundError):
            await GetMissionProviderResolutionHistory(
                InMemoryMissionRepository()
            ).execute(uuid4())

    asyncio.run(scenario())


def test_history_reads_legacy_resolved_event_without_snapshot() -> None:
    async def scenario() -> None:
        legacy_payload = ProviderResolvedEventPayload(
            provider_id="provider_a",
            mission_type=MissionType.TRAIN_TICKET,
            selection_mode=ProviderSelectionMode.automatic,
        )
        mission = make_mission(
            [
                ExecutionEvent(
                    timestamp=CURRENT_TIME,
                    type="provider_resolved",
                    message="Provider resolved for mission execution.",
                    metadata=legacy_payload.model_dump(
                        mode="json",
                        exclude_none=True,
                    ),
                )
            ]
        )
        repository = InMemoryMissionRepository()
        await repository.create(mission)

        history = await GetMissionProviderResolutionHistory(repository).execute(
            mission.id
        )

        assert history.items[0].payload.model_dump(mode="json")["snapshot"] is None

    asyncio.run(scenario())


def test_history_mapper_rejects_unrelated_event_type() -> None:
    with pytest.raises(ValueError, match="Unsupported provider history event"):
        provider_event_to_history_item(
            ExecutionEvent(
                timestamp=CURRENT_TIME,
                type="mission_started",
                message="Mission started.",
            )
        )


@pytest.fixture(autouse=True)
def clear_api_state() -> None:
    asyncio.run(mission_repository.clear())
    yield
    asyncio.run(mission_repository.clear())


def test_history_api_serializes_persisted_snapshot_without_mutation() -> None:
    mission = make_mission(make_provider_events())
    asyncio.run(mission_repository.create(mission))
    client = TestClient(app)

    response = client.get(f"/missions/{mission.id}/provider-resolution-history")

    assert response.status_code == 200
    assert [item["event_type"] for item in response.json()["items"]] == [
        "provider_resolution_failed",
        "provider_selection_changed",
        "provider_resolved",
    ]
    assert set(response.json()["items"][-1]) == {
        "event_type",
        "occurred_at",
        "payload",
    }
    assert response.json()["items"][-1]["payload"]["snapshot"] == {
        "selection_mode": "explicit",
        "requested_provider_id": "provider_b",
        "resolved_provider_id": "provider_b",
        "candidate_provider_ids": ["provider_b"],
        "mission_type": "train_ticket",
    }
    stored = asyncio.run(mission_repository.get(mission.id))
    assert stored is not None
    assert stored.execution_log == mission.execution_log


def test_history_api_returns_404_for_missing_mission() -> None:
    client = TestClient(app)

    response = client.get(f"/missions/{uuid4()}/provider-resolution-history")

    assert response.status_code == 404


def test_history_api_returns_empty_items_for_existing_mission() -> None:
    mission = make_mission()
    asyncio.run(mission_repository.create(mission))
    client = TestClient(app)

    response = client.get(f"/missions/{mission.id}/provider-resolution-history")

    assert response.status_code == 200
    assert response.json() == {
        "mission_id": str(mission.id),
        "items": [],
    }

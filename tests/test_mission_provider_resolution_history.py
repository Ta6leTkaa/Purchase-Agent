import asyncio
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

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
    DEFAULT_PROVIDER_HISTORY_PAGE_SIZE,
    GetMissionProviderResolutionHistory,
    InvalidProviderHistoryCursorError,
    MAX_PROVIDER_HISTORY_PAGE_SIZE,
    ProviderHistoryCursorCodec,
    ProviderHistoryEventType,
    ProviderResolutionHistoryPageRequest,
    provider_event_to_history_item,
)
from app.storage.memory import InMemoryMissionRepository

CURRENT_TIME = datetime(2026, 7, 23, 10, 0, tzinfo=timezone.utc)


def make_mission(
    execution_log: list[ExecutionEvent] | None = None,
) -> Mission:
    events = execution_log or []
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
        last_event_sequence=events[-1].sequence if events else 0,
        execution_log=events,
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
            sequence=1,
            timestamp=CURRENT_TIME + timedelta(minutes=4),
            type="mission_completed",
            message="Mission completed.",
        ),
        ExecutionEvent(
            sequence=2,
            timestamp=CURRENT_TIME,
            type="provider_resolution_failed",
            message="Provider resolution failed.",
            metadata=failure_payload.model_dump(mode="json"),
        ),
        ExecutionEvent(
            sequence=3,
            timestamp=CURRENT_TIME + timedelta(minutes=2),
            type="provider_resolved",
            message="Provider resolved for mission execution.",
            metadata=resolved_payload.model_dump(mode="json"),
        ),
        ExecutionEvent(
            sequence=4,
            timestamp=CURRENT_TIME + timedelta(minutes=1),
            type="provider_selection_changed",
            message="Mission provider selection changed.",
            metadata=selection_payload.model_dump(mode="json"),
        ),
        ExecutionEvent(
            sequence=5,
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


def test_history_uses_exclusive_cursor_pagination() -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()
        mission = make_mission(make_provider_events())
        await repository.create(mission)
        history_query = GetMissionProviderResolutionHistory(repository)

        first_page = await history_query.execute(
            mission.id,
            ProviderResolutionHistoryPageRequest(limit=2),
        )
        assert first_page.has_more is True
        assert first_page.next_cursor is not None
        assert [item.event_type for item in first_page.items] == [
            ProviderHistoryEventType.provider_resolution_failed,
            ProviderHistoryEventType.provider_selection_changed,
        ]

        second_page = await history_query.execute(
            mission.id,
            ProviderResolutionHistoryPageRequest(
                limit=2,
                cursor=first_page.next_cursor,
            ),
        )
        assert second_page.has_more is False
        assert second_page.next_cursor is None
        assert [item.event_type for item in second_page.items] == [
            ProviderHistoryEventType.provider_resolved,
        ]

    asyncio.run(scenario())


@pytest.mark.parametrize("limit", [0, -1, MAX_PROVIDER_HISTORY_PAGE_SIZE + 1])
def test_history_page_request_rejects_invalid_limits(limit: int) -> None:
    with pytest.raises(ValidationError):
        ProviderResolutionHistoryPageRequest(limit=limit)


def test_history_cursor_codec_rejects_malformed_cursor() -> None:
    with pytest.raises(InvalidProviderHistoryCursorError):
        ProviderHistoryCursorCodec().decode("not-a-cursor")


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
                    sequence=1,
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
                sequence=1,
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
    assert response.json()["page"] == {
        "limit": DEFAULT_PROVIDER_HISTORY_PAGE_SIZE,
        "has_more": False,
        "next_cursor": None,
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
        "page": {
            "limit": DEFAULT_PROVIDER_HISTORY_PAGE_SIZE,
            "has_more": False,
            "next_cursor": None,
        },
    }


def test_history_api_traverses_pages_without_duplicate_events() -> None:
    mission = make_mission(make_provider_events())
    asyncio.run(mission_repository.create(mission))
    client = TestClient(app)

    first_response = client.get(
        f"/missions/{mission.id}/provider-resolution-history?limit=2"
    )

    assert first_response.status_code == 200
    first_page = first_response.json()
    assert first_page["page"]["has_more"] is True
    assert first_page["page"]["limit"] == 2
    cursor = first_page["page"]["next_cursor"]
    assert cursor is not None

    second_response = client.get(
        f"/missions/{mission.id}/provider-resolution-history",
        params={"limit": 2, "cursor": cursor},
    )

    assert second_response.status_code == 200
    second_page = second_response.json()
    assert second_page["page"] == {
        "limit": 2,
        "has_more": False,
        "next_cursor": None,
    }
    event_types = [
        item["event_type"]
        for page in (first_page, second_page)
        for item in page["items"]
    ]
    assert event_types == [
        "provider_resolution_failed",
        "provider_selection_changed",
        "provider_resolved",
    ]


@pytest.mark.parametrize("limit", ["0", "-1", "101", "not-a-number"])
def test_history_api_rejects_invalid_limits(limit: str) -> None:
    client = TestClient(app)

    response = client.get(
        f"/missions/{uuid4()}/provider-resolution-history",
        params={"limit": limit},
    )

    assert response.status_code == 422


def test_history_api_rejects_malformed_cursor() -> None:
    mission = make_mission(make_provider_events())
    asyncio.run(mission_repository.create(mission))
    client = TestClient(app)

    response = client.get(
        f"/missions/{mission.id}/provider-resolution-history",
        params={"cursor": "invalid"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_cursor"

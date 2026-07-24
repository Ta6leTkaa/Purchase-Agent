import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Callable
from uuid import UUID, uuid4

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
    AsyncWaiter,
    DEFAULT_PROVIDER_HISTORY_PAGE_SIZE,
    GetMissionProviderResolutionHistory,
    GetMissionProviderResolutionIncrement,
    InvalidProviderHistoryCursorError,
    MAX_PROVIDER_HISTORY_PAGE_SIZE,
    ProviderHistoryCursorCodec,
    ProviderHistoryEventType,
    ProviderResolutionHistoryPageRequest,
    ProviderResolutionIncrementRequest,
    StaticMissionReadRepositoryFactory,
    provider_event_to_history_item,
)
from app.storage.memory import InMemoryMissionRepository

CURRENT_TIME = datetime(2026, 7, 23, 10, 0, tzinfo=timezone.utc)


class FakeAsyncWaiter(AsyncWaiter):
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[timedelta] = []
        self.on_sleep: Callable[[], None] | None = None

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, delay: timedelta) -> None:
        self.sleeps.append(delay)
        self.now += delay.total_seconds()
        if self.on_sleep is not None:
            self.on_sleep()


class CountingInMemoryMissionRepository(InMemoryMissionRepository):
    def __init__(self) -> None:
        super().__init__()
        self.get_calls = 0

    async def get(self, mission_id: UUID) -> Mission | None:
        self.get_calls += 1
        return await super().get(mission_id)


def build_increment_service(
    repository: InMemoryMissionRepository,
    waiter: FakeAsyncWaiter,
    *,
    poll_interval: timedelta = timedelta(milliseconds=500),
) -> GetMissionProviderResolutionIncrement:
    return GetMissionProviderResolutionIncrement(
        StaticMissionReadRepositoryFactory(repository),
        waiter,
        poll_interval,
    )


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


def test_increment_returns_provider_events_after_sequence_in_order() -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()
        mission = make_mission(make_provider_events())
        await repository.create(mission)

        increment = await build_increment_service(
            repository,
            FakeAsyncWaiter(),
        ).execute(
            mission.id,
            ProviderResolutionIncrementRequest(since_sequence=2),
        )

        assert [item.sequence for item in increment.items] == [3, 4]
        assert [item.event_type for item in increment.items] == [
            ProviderHistoryEventType.provider_resolved,
            ProviderHistoryEventType.provider_selection_changed,
        ]
        assert increment.latest_sequence == 4

    asyncio.run(scenario())


def test_increment_returns_empty_result_after_high_sequence() -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()
        mission = make_mission(make_provider_events())
        await repository.create(mission)

        increment = await build_increment_service(
            repository,
            FakeAsyncWaiter(),
        ).execute(
            mission.id,
            ProviderResolutionIncrementRequest(since_sequence=999),
        )

        assert increment.items == ()
        assert increment.latest_sequence == 999

    asyncio.run(scenario())


@pytest.mark.parametrize("limit", [0, -1, MAX_PROVIDER_HISTORY_PAGE_SIZE + 1])
def test_history_page_request_rejects_invalid_limits(limit: int) -> None:
    with pytest.raises(ValidationError):
        ProviderResolutionHistoryPageRequest(limit=limit)


def test_increment_request_rejects_negative_sequence() -> None:
    with pytest.raises(ValidationError):
        ProviderResolutionIncrementRequest(since_sequence=-1)


@pytest.mark.parametrize(
    "wait_timeout",
    [timedelta(seconds=-1), timedelta(seconds=31)],
)
def test_increment_request_rejects_invalid_wait_timeout(
    wait_timeout: timedelta,
) -> None:
    with pytest.raises(ValidationError):
        ProviderResolutionIncrementRequest(
            since_sequence=0,
            wait_timeout=wait_timeout,
        )


def test_long_poll_returns_existing_events_without_sleep() -> None:
    async def scenario() -> None:
        repository = CountingInMemoryMissionRepository()
        mission = make_mission(make_provider_events())
        await repository.create(mission)
        waiter = FakeAsyncWaiter()

        increment = await build_increment_service(repository, waiter).execute(
            mission.id,
            ProviderResolutionIncrementRequest(
                since_sequence=0,
                wait_timeout=timedelta(seconds=20),
            ),
        )

        assert [item.sequence for item in increment.items] == [2, 3, 4]
        assert waiter.sleeps == []
        assert repository.get_calls == 1

    asyncio.run(scenario())


def test_long_poll_reads_fresh_events_after_one_interval() -> None:
    async def scenario() -> None:
        repository = CountingInMemoryMissionRepository()
        mission = make_mission()
        await repository.create(mission)
        waiter = FakeAsyncWaiter()

        def add_provider_event() -> None:
            mission.execution_log = make_provider_events()
            mission.last_event_sequence = 5

        waiter.on_sleep = add_provider_event
        increment = await build_increment_service(repository, waiter).execute(
            mission.id,
            ProviderResolutionIncrementRequest(
                since_sequence=0,
                wait_timeout=timedelta(seconds=2),
            ),
        )

        assert [item.sequence for item in increment.items] == [2, 3, 4]
        assert waiter.sleeps == [timedelta(milliseconds=500)]
        assert repository.get_calls == 2

    asyncio.run(scenario())


def test_long_poll_timeout_performs_final_read_at_deadline() -> None:
    async def scenario() -> None:
        repository = CountingInMemoryMissionRepository()
        mission = make_mission()
        await repository.create(mission)
        waiter = FakeAsyncWaiter()

        increment = await build_increment_service(
            repository,
            waiter,
            poll_interval=timedelta(milliseconds=500),
        ).execute(
            mission.id,
            ProviderResolutionIncrementRequest(
                since_sequence=0,
                wait_timeout=timedelta(milliseconds=200),
            ),
        )

        assert increment.items == ()
        assert increment.latest_sequence == 0
        assert waiter.sleeps == [timedelta(milliseconds=200)]
        assert repository.get_calls == 2

    asyncio.run(scenario())


def test_long_poll_returns_missing_mission_without_waiting() -> None:
    async def scenario() -> None:
        repository = CountingInMemoryMissionRepository()
        waiter = FakeAsyncWaiter()

        with pytest.raises(MissionNotFoundError):
            await build_increment_service(repository, waiter).execute(
                uuid4(),
                ProviderResolutionIncrementRequest(
                    since_sequence=0,
                    wait_timeout=timedelta(seconds=20),
                ),
            )

        assert repository.get_calls == 1
        assert waiter.sleeps == []

    asyncio.run(scenario())


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
        "sequence",
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


def test_increment_api_returns_snapshot_and_latest_sequence() -> None:
    mission = make_mission(make_provider_events())
    asyncio.run(mission_repository.create(mission))
    client = TestClient(app)

    response = client.get(
        f"/missions/{mission.id}/provider-resolution-history/since/2"
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["since_sequence"] == 2
    assert response.json()["latest_sequence"] == 4
    assert [item["sequence"] for item in response.json()["items"]] == [3, 4]
    assert response.json()["items"][0]["payload"]["snapshot"] == {
        "selection_mode": "explicit",
        "requested_provider_id": "provider_b",
        "resolved_provider_id": "provider_b",
        "candidate_provider_ids": ["provider_b"],
        "mission_type": "train_ticket",
    }


def test_increment_api_returns_empty_result_for_existing_mission() -> None:
    mission = make_mission()
    asyncio.run(mission_repository.create(mission))
    client = TestClient(app)

    response = client.get(
        f"/missions/{mission.id}/provider-resolution-history/since/0"
    )

    assert response.status_code == 200
    assert response.json() == {
        "mission_id": str(mission.id),
        "since_sequence": 0,
        "latest_sequence": 0,
        "items": [],
    }


def test_increment_api_returns_404_for_missing_mission() -> None:
    client = TestClient(app)

    response = client.get(
        f"/missions/{uuid4()}/provider-resolution-history/since/0",
        params={"wait_seconds": 20},
    )

    assert response.status_code == 404


@pytest.mark.parametrize("sequence", ["-1", "not-a-number"])
def test_increment_api_rejects_invalid_sequence(sequence: str) -> None:
    client = TestClient(app)

    response = client.get(
        f"/missions/{uuid4()}/provider-resolution-history/since/{sequence}"
    )

    assert response.status_code == 422


@pytest.mark.parametrize("wait_seconds", ["-1", "31", "not-a-number"])
def test_increment_api_rejects_invalid_wait_seconds(
    wait_seconds: str,
) -> None:
    client = TestClient(app)

    response = client.get(
        f"/missions/{uuid4()}/provider-resolution-history/since/0",
        params={"wait_seconds": wait_seconds},
    )

    assert response.status_code == 422


def test_increment_api_returns_existing_events_immediately_with_wait() -> None:
    mission = make_mission(make_provider_events())
    asyncio.run(mission_repository.create(mission))
    client = TestClient(app)

    response = client.get(
        f"/missions/{mission.id}/provider-resolution-history/since/2",
        params={"wait_seconds": 20},
    )

    assert response.status_code == 200
    assert [item["sequence"] for item in response.json()["items"]] == [3, 4]

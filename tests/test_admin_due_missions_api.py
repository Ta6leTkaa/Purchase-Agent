import asyncio
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import settings
from app.dependencies import get_current_time, identity_repository, mission_repository
from app.domain.identity import Identity
from app.domain.mission import (
    FallbackRules,
    Mission,
    MissionStatus,
    MissionType,
    TrainConstraints,
)
from app.main import app

CURRENT_TIME = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
ADMIN_HEADERS = {"X-Admin-API-Key": "test-admin-key"}


@pytest.fixture(autouse=True)
def clear_repositories() -> Iterator[None]:
    original_admin_api_key = settings.admin_api_key
    settings.admin_api_key = SecretStr("test-admin-key")
    asyncio.run(identity_repository.clear())
    asyncio.run(mission_repository.clear())
    app.dependency_overrides[get_current_time] = lambda: CURRENT_TIME
    yield
    app.dependency_overrides.clear()
    settings.admin_api_key = original_admin_api_key
    asyncio.run(identity_repository.clear())
    asyncio.run(mission_repository.clear())


def test_process_due_returns_empty_result_without_due_missions() -> None:
    client = TestClient(app)

    response = client.post(
        "/admin/missions/process-due",
        json={},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    assert response.json() == {
        "processed_count": 0,
        "expired_mission_ids": [],
        "succeeded_mission_ids": [],
        "failed_mission_ids": [],
        "retry_scheduled_mission_ids": [],
        "errors": {},
    }


def test_mission_statistics_reports_actionable_worker_backlog() -> None:
    participant_ids = create_identities(4)
    due = create_mission(participant_ids, scheduled_at=CURRENT_TIME)
    exhausted = create_mission(
        participant_ids,
        scheduled_at=CURRENT_TIME - timedelta(minutes=2),
    )
    exhausted.execution_attempts = exhausted.max_execution_attempts
    expired = create_mission(
        participant_ids,
        scheduled_at=CURRENT_TIME - timedelta(hours=2),
    )
    expired.status = MissionStatus.paused
    expired.expires_at = CURRENT_TIME - timedelta(hours=1)
    stale = create_mission(
        participant_ids,
        scheduled_at=CURRENT_TIME - timedelta(minutes=30),
    )
    stale.status = MissionStatus.processing
    stale.claimed_at = CURRENT_TIME - timedelta(minutes=16)

    response = TestClient(app).get(
        "/admin/mission-statistics",
        params={"claim_timeout_seconds": 900},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    assert response.json() == {
        "generated_at": "2026-08-01T10:00:00Z",
        "total_missions": 4,
        "missions_by_status": {
            "waiting": 2,
            "paused": 1,
            "processing": 1,
        },
        "due_missions": 1,
        "expired_pending_missions": 1,
        "stale_processing_missions": 1,
        "exhausted_waiting_missions": 1,
        "claim_timeout_seconds": 900,
    }
    assert due.status is MissionStatus.waiting


@pytest.mark.parametrize("claim_timeout_seconds", [0, 86401])
def test_mission_statistics_rejects_invalid_claim_timeout(
    claim_timeout_seconds: int,
) -> None:
    response = TestClient(app).get(
        "/admin/mission-statistics",
        params={"claim_timeout_seconds": claim_timeout_seconds},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "path",
    [
        "/admin/notification-outbox",
        "/admin/notification-outbox/statistics",
    ],
)
def test_notification_outbox_is_unavailable_with_memory_storage(
    path: str,
) -> None:
    client = TestClient(app)

    response = client.get(
        path,
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Notification outbox requires the database storage backend"
    }


def test_notification_recovery_is_unavailable_with_memory_storage() -> None:
    client = TestClient(app)

    response = client.post(
        "/admin/notification-outbox/recover-stale",
        json={},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Notification outbox requires the database storage backend"
    }


def test_notification_outbox_page_rejects_invalid_cursor() -> None:
    client = TestClient(app)

    response = client.get(
        "/admin/notification-outbox/page",
        params={"cursor": "invalid"},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == (
        "invalid_notification_outbox_cursor"
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"claim_timeout_seconds": 0},
        {"claim_timeout_seconds": 86401},
        {"limit": 0},
        {"limit": 501},
        {"unknown": True},
    ],
)
def test_notification_recovery_rejects_invalid_request(
    payload: dict[str, object],
) -> None:
    client = TestClient(app)

    response = client.post(
        "/admin/notification-outbox/recover-stale",
        json=payload,
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 422


def test_process_due_runs_due_mission() -> None:
    client = TestClient(app)
    participant_ids = create_identities(4)
    mission = create_mission(
        participant_ids,
        scheduled_at=CURRENT_TIME,
    )

    response = client.post(
        "/admin/missions/process-due",
        json={},
        headers=ADMIN_HEADERS,
    )
    stored_mission = asyncio.run(mission_repository.get(mission.id))

    assert response.status_code == 200
    assert response.json()["processed_count"] == 1
    assert response.json()["succeeded_mission_ids"] == [str(mission.id)]
    assert response.json()["failed_mission_ids"] == []
    assert response.json()["errors"] == {}
    assert stored_mission is not None
    assert stored_mission.status is MissionStatus.requires_confirmation
    assert stored_mission.execution_attempts == 1
    assert stored_mission.best_option is not None
    assert stored_mission.best_option.train_number == "001A"


def test_process_due_skips_future_mission() -> None:
    client = TestClient(app)
    participant_ids = create_identities(4)
    mission = create_mission(
        participant_ids,
        scheduled_at=CURRENT_TIME + timedelta(minutes=1),
    )

    response = client.post(
        "/admin/missions/process-due",
        json={},
        headers=ADMIN_HEADERS,
    )
    stored_mission = asyncio.run(mission_repository.get(mission.id))

    assert response.status_code == 200
    assert response.json()["processed_count"] == 0
    assert stored_mission is not None
    assert stored_mission.status is MissionStatus.waiting
    assert stored_mission.execution_log == []


def test_process_due_passes_limit_to_processor() -> None:
    client = TestClient(app)
    participant_ids = create_identities(4)
    first_mission = create_mission(
        participant_ids,
        scheduled_at=CURRENT_TIME - timedelta(minutes=2),
    )
    second_mission = create_mission(
        participant_ids,
        scheduled_at=CURRENT_TIME - timedelta(minutes=1),
    )

    response = client.post(
        "/admin/missions/process-due",
        json={"limit": 1},
        headers=ADMIN_HEADERS,
    )
    stored_first_mission = asyncio.run(
        mission_repository.get(first_mission.id)
    )
    stored_second_mission = asyncio.run(
        mission_repository.get(second_mission.id)
    )

    assert response.status_code == 200
    assert response.json()["processed_count"] == 1
    assert response.json()["succeeded_mission_ids"] == [
        str(first_mission.id)
    ]
    assert stored_first_mission is not None
    assert stored_first_mission.status is MissionStatus.requires_confirmation
    assert stored_second_mission is not None
    assert stored_second_mission.status is MissionStatus.waiting


def test_process_due_does_not_process_same_mission_twice() -> None:
    client = TestClient(app)
    participant_ids = create_identities(4)
    mission = create_mission(
        participant_ids,
        scheduled_at=CURRENT_TIME,
    )

    first_response = client.post(
        "/admin/missions/process-due",
        json={},
        headers=ADMIN_HEADERS,
    )
    second_response = client.post(
        "/admin/missions/process-due",
        json={},
        headers=ADMIN_HEADERS,
    )
    stored_mission = asyncio.run(mission_repository.get(mission.id))

    assert first_response.status_code == 200
    assert first_response.json()["processed_count"] == 1
    assert second_response.status_code == 200
    assert second_response.json()["processed_count"] == 0
    assert stored_mission is not None
    assert stored_mission.status is MissionStatus.requires_confirmation


@pytest.mark.parametrize("limit", [0, 501])
def test_process_due_rejects_invalid_limit(limit: int) -> None:
    client = TestClient(app)

    response = client.post(
        "/admin/missions/process-due",
        json={"limit": limit},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 422


def create_identities(count: int) -> list[UUID]:
    identities = [
        Identity(
            id=uuid4(),
            display_name="Ivan Petrov",
            first_name="Ivan",
            last_name="Petrov",
            birth_date=date(1990, 1, 1),
        )
        for _ in range(count)
    ]
    for identity in identities:
        asyncio.run(identity_repository.create(identity))
    return [identity.id for identity in identities]


def create_mission(
    participant_ids: list[UUID],
    scheduled_at: datetime,
) -> Mission:
    mission = Mission(
        id=uuid4(),
        type=MissionType.train_trip,
        title="Moscow to Saint Petersburg",
        status=MissionStatus.waiting,
        participant_ids=participant_ids,
        provider="mock_train",
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Saint Petersburg",
            travel_date=date(2026, 8, 1),
            passengers_count=len(participant_ids),
            must_be_same_compartment=True,
            min_lower_berths=2,
            max_total_price=30000,
            avoid_toilet=True,
        ),
        fallback_rules=FallbackRules(allow_adjacent_compartments=True),
        scheduled_at=scheduled_at,
    )
    return asyncio.run(mission_repository.create(mission))

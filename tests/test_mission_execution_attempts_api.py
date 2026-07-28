import asyncio
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.dependencies import mission_repository
from app.domain.mission import (
    Mission,
    MissionStatus,
    TrainConstraints,
    TrainTicketMissionPayload,
)
from app.main import app


@pytest.fixture(autouse=True)
def clear_missions() -> Iterator[None]:
    asyncio.run(mission_repository.clear())
    yield
    asyncio.run(mission_repository.clear())


def test_execution_attempt_history_returns_claim_audit_records() -> None:
    async def scenario() -> Mission:
        current_time = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
        mission = make_due_mission(current_time)
        await mission_repository.create(mission)
        claimed = (await mission_repository.claim_due(current_time))[0]
        claimed.resolved_provider_id = "mock_train"
        await mission_repository.update(claimed)
        return claimed

    claimed = asyncio.run(scenario())
    response = TestClient(app).get(
        f"/missions/{claimed.id}/execution-attempts"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["mission_id"] == str(claimed.id)
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["attempt_number"] == 1
    assert item["status"] == "processing"
    assert item["claimed_at"].startswith("2026-07-28T12:00:00")
    assert item["finished_at"] is None
    assert item["resolved_provider_id"] == "mock_train"


def test_execution_attempt_history_is_read_only_and_returns_404() -> None:
    async def scenario() -> Mission:
        current_time = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
        mission = make_due_mission(current_time)
        await mission_repository.create(mission)
        return (await mission_repository.claim_due(current_time))[0]

    claimed = asyncio.run(scenario())
    client = TestClient(app)

    response = client.get(f"/missions/{claimed.id}/execution-attempts")

    assert response.status_code == 200
    stored = asyncio.run(mission_repository.get(claimed.id))
    assert stored is not None
    assert stored.status is MissionStatus.processing
    assert stored.claimed_at == claimed.claimed_at
    assert stored.execution_log == claimed.execution_log

    missing_response = client.get(
        f"/missions/{uuid4()}/execution-attempts"
    )
    assert missing_response.status_code == 404


def test_execution_attempt_history_is_empty_before_first_claim() -> None:
    async def scenario() -> Mission:
        current_time = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
        mission = make_due_mission(current_time)
        await mission_repository.create(mission)
        return mission

    mission = asyncio.run(scenario())
    response = TestClient(app).get(
        f"/missions/{mission.id}/execution-attempts"
    )

    assert response.status_code == 200
    assert response.json() == {
        "mission_id": str(mission.id),
        "items": [],
    }


def test_execution_attempt_history_preserves_attempt_order_and_outcome() -> None:
    async def scenario() -> Mission:
        current_time = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
        mission = make_due_mission(current_time)
        await mission_repository.create(mission)
        await mission_repository.claim_due(current_time)
        await mission_repository.recover_stale_processing(
            current_time + timedelta(minutes=16),
            claim_timeout=timedelta(minutes=15),
        )
        return (
            await mission_repository.claim_due(
                current_time + timedelta(minutes=17)
            )
        )[0]

    claimed = asyncio.run(scenario())
    response = TestClient(app).get(
        f"/missions/{claimed.id}/execution-attempts"
    )

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["attempt_number"] for item in items] == [1, 2]
    assert [item["status"] for item in items] == ["recovered", "processing"]
    assert items[0]["finished_at"].startswith("2026-07-28T12:16:00")
    assert items[1]["finished_at"] is None


def make_due_mission(current_time: datetime) -> Mission:
    return Mission(
        id=uuid4(),
        title="Amsterdam to Berlin",
        status=MissionStatus.waiting,
        participant_ids=[uuid4()],
        provider="mock_train",
        scheduled_at=current_time,
        constraints=TrainConstraints(
            from_city="Amsterdam",
            to_city="Berlin",
            travel_date=date(2026, 8, 1),
            passengers_count=1,
        ),
        payload=TrainTicketMissionPayload(
            origin="Amsterdam",
            destination="Berlin",
            departure_date=date(2026, 8, 1),
        ),
    )

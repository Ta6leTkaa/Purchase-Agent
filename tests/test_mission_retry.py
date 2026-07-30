import asyncio
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.dependencies import (
    mission_command_idempotency_store,
    mission_repository,
)
from app.domain.mission import (
    Mission,
    MissionStatus,
    MissionType,
    TrainConstraints,
)
from app.main import app
from app.services.mission_errors import MissionNotFoundError
from app.services.mission_retry import (
    InvalidMissionRetryTimeError,
    MissionAttemptsExhaustedError,
    MissionRetryNotAllowedError,
    retry_mission,
)
from app.storage.memory import InMemoryMissionRepository

CURRENT_TIME = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)


def make_mission(
    *,
    status: MissionStatus = MissionStatus.failed,
    execution_attempts: int = 1,
    max_execution_attempts: int = 3,
) -> Mission:
    return Mission(
        id=uuid4(),
        type=MissionType.TRAIN_TICKET,
        title="Retry train purchase",
        status=status,
        participant_ids=[uuid4()],
        provider="mock_train",
        resolved_provider_id="mock_train",
        reservation_id="stale-reservation",
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Saint Petersburg",
            travel_date=date(2026, 8, 1),
            passengers_count=1,
        ),
        execution_attempts=execution_attempts,
        max_execution_attempts=max_execution_attempts,
    )


def test_retry_failed_mission_schedules_new_claim_and_clears_stale_result() -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()
        mission = make_mission()
        await repository.create(mission)
        retry_at = CURRENT_TIME + timedelta(minutes=10)

        retried = await retry_mission(
            mission.id,
            repository,
            retry_at=retry_at,
            current_time=CURRENT_TIME,
        )

        assert retried.status is MissionStatus.waiting
        assert retried.scheduled_at == retry_at
        assert retried.execution_attempts == 1
        assert retried.resolved_provider_id is None
        assert retried.reservation_id is None
        assert retried.best_option is None
        event = retried.execution_log[-1]
        assert event.type == "mission_retry_scheduled"
        assert event.metadata == {
            "retry_at": retry_at.isoformat(),
            "execution_attempts": 1,
            "max_execution_attempts": 3,
            "previous_resolved_provider_id": "mock_train",
            "previous_reservation_id": "stale-reservation",
            "trigger": "manual",
            "reason": None,
        }
        assert await repository.list_due(CURRENT_TIME) == []
        assert [item.id for item in await repository.list_due(retry_at)] == [
            mission.id
        ]

    asyncio.run(scenario())


def test_retry_without_retry_at_is_immediately_due() -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()
        mission = make_mission()
        await repository.create(mission)

        retried = await retry_mission(
            mission.id,
            repository,
            current_time=CURRENT_TIME,
        )

        assert retried.scheduled_at == CURRENT_TIME
        claimed = await repository.claim_due(CURRENT_TIME)
        assert [item.id for item in claimed] == [mission.id]
        assert claimed[0].execution_attempts == 2

    asyncio.run(scenario())


def test_retry_rejects_wrong_state_exhausted_attempts_and_missing_mission() -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()
        active = make_mission(status=MissionStatus.created, execution_attempts=0)
        exhausted = make_mission(execution_attempts=3)
        await repository.create(active)
        await repository.create(exhausted)

        with pytest.raises(MissionRetryNotAllowedError):
            await retry_mission(
                active.id,
                repository,
                current_time=CURRENT_TIME,
            )
        with pytest.raises(MissionAttemptsExhaustedError):
            await retry_mission(
                exhausted.id,
                repository,
                current_time=CURRENT_TIME,
            )
        with pytest.raises(MissionNotFoundError):
            await retry_mission(
                uuid4(),
                repository,
                current_time=CURRENT_TIME,
            )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "retry_at",
    [
        CURRENT_TIME - timedelta(seconds=1),
        datetime(2026, 7, 29, 12, 1),
    ],
)
def test_retry_rejects_invalid_time(retry_at: datetime) -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()
        mission = make_mission()
        await repository.create(mission)

        with pytest.raises(InvalidMissionRetryTimeError):
            await retry_mission(
                mission.id,
                repository,
                retry_at=retry_at,
                current_time=CURRENT_TIME,
            )

        assert mission.status is MissionStatus.failed
        assert mission.execution_log == []

    asyncio.run(scenario())


@pytest.fixture(autouse=True)
def clear_api_state() -> Iterator[None]:
    asyncio.run(mission_repository.clear())
    asyncio.run(mission_command_idempotency_store.clear())
    yield
    asyncio.run(mission_repository.clear())
    asyncio.run(mission_command_idempotency_store.clear())


def test_retry_api_returns_waiting_mission_etag_and_idempotent_replay() -> None:
    mission = make_mission()
    asyncio.run(mission_repository.create(mission))
    client = TestClient(app)
    headers = {
        "Idempotency-Key": "retry-failed-mission",
        "If-Match": '"0"',
    }

    first = client.post(
        f"/missions/{mission.id}/retry",
        json={"retry_at": "2030-08-01T10:00:00Z"},
        headers=headers,
    )
    replay = client.post(
        f"/missions/{mission.id}/retry",
        json={"retry_at": "2030-08-01T10:00:00Z"},
        headers={
            "Idempotency-Key": "retry-failed-mission",
            "If-Match": first.headers["etag"],
        },
    )

    assert first.status_code == 200
    assert first.json()["status"] == "waiting"
    assert first.json()["scheduled_at"] == "2030-08-01T10:00:00Z"
    assert first.headers["etag"] == '"1"'
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert replay.headers["etag"] == '"1"'


def test_retry_api_maps_state_attempt_time_and_version_errors() -> None:
    active = make_mission(status=MissionStatus.created, execution_attempts=0)
    exhausted = make_mission(execution_attempts=3)
    asyncio.run(mission_repository.create(active))
    asyncio.run(mission_repository.create(exhausted))
    client = TestClient(app)

    wrong_state = client.post(
        f"/missions/{active.id}/retry",
        json={},
        headers={"Idempotency-Key": "retry-active"},
    )
    exhausted_attempts = client.post(
        f"/missions/{exhausted.id}/retry",
        json={},
        headers={"Idempotency-Key": "retry-exhausted"},
    )
    naive_time = client.post(
        f"/missions/{exhausted.id}/retry",
        json={"retry_at": "2030-08-01T10:00:00"},
        headers={"Idempotency-Key": "retry-naive"},
    )
    stale_version = client.post(
        f"/missions/{active.id}/retry",
        json={},
        headers={
            "Idempotency-Key": "retry-stale",
            "If-Match": '"8"',
        },
    )

    assert wrong_state.status_code == 409
    assert wrong_state.json()["detail"]["code"] == "mission_retry_not_allowed"
    assert exhausted_attempts.status_code == 409
    assert exhausted_attempts.json()["detail"]["code"] == (
        "mission_attempts_exhausted"
    )
    assert naive_time.status_code == 422
    assert stale_version.status_code == 409
    assert stale_version.json()["detail"]["code"] == "mission_version_conflict"

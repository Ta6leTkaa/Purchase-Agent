import asyncio
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import settings
from app.dependencies import (
    get_current_time,
    get_mission_repository,
    mission_repository,
)
from app.domain.mission import (
    Mission,
    MissionStatus,
    MissionType,
    TrainConstraints,
)
from app.main import app
from app.repositories.mission import MissionRepository
from app.storage.memory import InMemoryMissionRepository

CURRENT_TIME = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
ADMIN_HEADERS = {"X-Admin-API-Key": "test-admin-key"}
ENDPOINT = "/admin/missions/recover-stale"


@pytest.fixture(autouse=True)
def reset_state() -> Iterator[None]:
    original_admin_api_key = settings.admin_api_key
    settings.admin_api_key = SecretStr("test-admin-key")
    asyncio.run(mission_repository.clear())
    app.dependency_overrides[get_current_time] = lambda: CURRENT_TIME
    yield
    app.dependency_overrides.clear()
    settings.admin_api_key = original_admin_api_key
    asyncio.run(mission_repository.clear())


def test_recover_stale_recovers_only_eligible_missions() -> None:
    client = TestClient(app)
    stale_mission = create_mission(
        claimed_at=CURRENT_TIME - timedelta(minutes=16)
    )
    boundary_mission = create_mission(
        claimed_at=CURRENT_TIME - timedelta(minutes=15)
    )
    fresh_mission = create_mission(
        claimed_at=CURRENT_TIME - timedelta(minutes=14)
    )
    waiting_mission = create_mission(
        status=MissionStatus.waiting,
        claimed_at=None,
    )
    failed_mission = create_mission(
        status=MissionStatus.failed,
        claimed_at=None,
    )
    legacy_processing_mission = create_legacy_processing_mission()

    response = client.post(ENDPOINT, json={}, headers=ADMIN_HEADERS)

    assert response.status_code == 200
    assert response.json() == {
        "recovered_count": 2,
        "recovered_mission_ids": [
            str(stale_mission.id),
            str(boundary_mission.id),
        ],
    }
    assert_stored_status(stale_mission.id, MissionStatus.waiting, None)
    assert_stored_status(boundary_mission.id, MissionStatus.waiting, None)
    assert_stored_status(
        fresh_mission.id,
        MissionStatus.processing,
        CURRENT_TIME - timedelta(minutes=14),
    )
    assert_stored_status(waiting_mission.id, MissionStatus.waiting, None)
    assert_stored_status(failed_mission.id, MissionStatus.failed, None)
    assert_stored_status(
        legacy_processing_mission.id,
        MissionStatus.processing,
        None,
    )
    recovered_mission = asyncio.run(mission_repository.get(stale_mission.id))
    assert recovered_mission is not None
    assert recovered_mission.execution_log[-1].type == "claim_recovered"


def test_recover_stale_returns_empty_result() -> None:
    repository = CapturingMissionRepository()
    app.dependency_overrides[get_mission_repository] = lambda: repository
    client = TestClient(app)

    response = client.post(ENDPOINT, json={}, headers=ADMIN_HEADERS)

    assert response.status_code == 200
    assert response.json() == {
        "recovered_count": 0,
        "recovered_mission_ids": [],
    }
    assert repository.recovery_arguments == [
        (CURRENT_TIME, timedelta(seconds=900), 100)
    ]


def test_recover_stale_passes_custom_timeout_and_limit() -> None:
    repository = CapturingMissionRepository()
    app.dependency_overrides[get_mission_repository] = lambda: repository
    client = TestClient(app)
    oldest_mission = create_in_repository(
        repository,
        claimed_at=CURRENT_TIME - timedelta(minutes=31),
    )
    newer_mission = create_in_repository(
        repository,
        claimed_at=CURRENT_TIME - timedelta(minutes=30),
    )

    response = client.post(
        ENDPOINT,
        json={"claim_timeout_seconds": 1800, "limit": 1},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["recovered_mission_ids"] == [str(oldest_mission.id)]
    assert repository.recovery_arguments == [
        (CURRENT_TIME, timedelta(seconds=1800), 1)
    ]
    stored_newer_mission = asyncio.run(repository.get(newer_mission.id))
    assert stored_newer_mission is not None
    assert stored_newer_mission.status is MissionStatus.processing


@pytest.mark.parametrize(
    "payload",
    [
        {"claim_timeout_seconds": 0},
        {"claim_timeout_seconds": 86401},
        {"limit": 0},
        {"limit": 501},
        {"unexpected": True},
    ],
)
def test_recover_stale_rejects_invalid_request(payload: dict[str, object]) -> None:
    client = TestClient(app)

    response = client.post(ENDPOINT, json=payload, headers=ADMIN_HEADERS)

    assert response.status_code == 422


class CapturingMissionRepository(InMemoryMissionRepository):
    def __init__(self) -> None:
        super().__init__()
        self.recovery_arguments: list[tuple[datetime, timedelta, int]] = []

    async def recover_stale_processing(
        self,
        current_time: datetime,
        claim_timeout: timedelta,
        limit: int = 100,
    ) -> list[Mission]:
        self.recovery_arguments.append((current_time, claim_timeout, limit))
        return await super().recover_stale_processing(
            current_time,
            claim_timeout,
            limit,
        )


def create_mission(
    status: MissionStatus = MissionStatus.processing,
    claimed_at: datetime | None = None,
) -> Mission:
    return create_in_repository(
        mission_repository,
        status=status,
        claimed_at=claimed_at,
    )


def create_in_repository(
    repository: MissionRepository,
    status: MissionStatus = MissionStatus.processing,
    claimed_at: datetime | None = None,
) -> Mission:
    mission = Mission(
        id=uuid4(),
        type=MissionType.train_trip,
        title="Moscow to Saint Petersburg",
        status=status,
        participant_ids=[uuid4()],
        provider="mock_train",
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Saint Petersburg",
            travel_date=date(2026, 8, 1),
            passengers_count=1,
        ),
        scheduled_at=CURRENT_TIME - timedelta(hours=1),
        claimed_at=claimed_at,
    )
    return asyncio.run(repository.create(mission))


def create_legacy_processing_mission() -> Mission:
    mission = create_mission(claimed_at=CURRENT_TIME)
    legacy_mission = Mission.model_construct(
        **{**mission.model_dump(), "claimed_at": None}
    )
    return asyncio.run(mission_repository.update(legacy_mission))


def assert_stored_status(
    mission_id: UUID,
    status: MissionStatus,
    claimed_at: datetime | None,
) -> None:
    stored_mission = asyncio.run(mission_repository.get(mission_id))
    assert stored_mission is not None
    assert stored_mission.status is status
    assert stored_mission.claimed_at == claimed_at

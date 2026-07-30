import asyncio
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.mission import (
    Mission,
    MissionStatus,
    TrainConstraints,
)
from app.services.mission_expiration import expire_due_missions
from app.storage.memory import InMemoryMissionRepository

NOW = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)


def make_mission(
    *,
    status: MissionStatus,
    expires_at: datetime | None,
) -> Mission:
    return Mission(
        id=uuid4(),
        title="Expiring mission",
        status=status,
        participant_ids=[uuid4()],
        provider="mock_train",
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Saint Petersburg",
            travel_date=date(2026, 8, 1),
            passengers_count=1,
        ),
        expires_at=expires_at,
    )


def test_expire_due_missions_expires_planning_states_only() -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()
        due = [
            make_mission(
                status=status,
                expires_at=NOW - timedelta(seconds=1),
            )
            for status in (
                MissionStatus.created,
                MissionStatus.waiting,
                MissionStatus.paused,
            )
        ]
        future = make_mission(
            status=MissionStatus.waiting,
            expires_at=NOW + timedelta(seconds=1),
        )
        for mission in [*due, future]:
            await repository.create(mission)

        expired = await expire_due_missions(repository, NOW)

        assert {mission.id for mission in expired} == {
            mission.id for mission in due
        }
        assert future.status is MissionStatus.waiting
        for mission in due:
            assert mission.status is MissionStatus.expired
            event = mission.execution_log[-1]
            assert event.type == "mission_expired"
            assert event.timestamp == NOW
            assert event.metadata["expires_at"] == (
                NOW - timedelta(seconds=1)
            ).isoformat()

    asyncio.run(scenario())


def test_expire_due_missions_respects_limit() -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()
        for _ in range(3):
            await repository.create(
                make_mission(
                    status=MissionStatus.created,
                    expires_at=NOW,
                )
            )

        expired = await expire_due_missions(repository, NOW, limit=2)

        assert len(expired) == 2

    asyncio.run(scenario())


def test_expire_due_missions_validates_arguments() -> None:
    repository = InMemoryMissionRepository()

    with pytest.raises(ValueError):
        asyncio.run(
            expire_due_missions(
                repository,
                datetime(2026, 7, 29, 12, 0),
            )
        )
    with pytest.raises(ValueError):
        asyncio.run(expire_due_missions(repository, NOW, limit=0))

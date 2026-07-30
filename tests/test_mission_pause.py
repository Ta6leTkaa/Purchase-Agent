import asyncio
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from app.domain.mission import (
    Mission,
    MissionStatus,
    TrainConstraints,
)
from app.services.mission_pause import (
    MissionPauseNotAllowedError,
    MissionResumeNotAllowedError,
    pause_mission,
    resume_mission,
)
from app.storage.memory import InMemoryMissionRepository

NOW = datetime(2026, 7, 29, 14, 0, tzinfo=UTC)
SCHEDULED_AT = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)


def make_mission(
    *,
    status: MissionStatus = MissionStatus.created,
    scheduled_at: datetime | None = None,
) -> Mission:
    return Mission(
        id=uuid4(),
        title="Family train trip",
        status=status,
        participant_ids=[uuid4()],
        provider="mock_train",
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Saint Petersburg",
            travel_date=date(2026, 8, 1),
            passengers_count=1,
        ),
        scheduled_at=scheduled_at,
    )


@pytest.mark.parametrize(
    "status",
    [MissionStatus.created, MissionStatus.waiting],
)
def test_pause_mission_records_previous_status(status: MissionStatus) -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()
        mission = make_mission(
            status=status,
            scheduled_at=(
                SCHEDULED_AT
                if status is MissionStatus.waiting
                else None
            ),
        )
        await repository.create(mission)

        paused = await pause_mission(
            mission.id,
            repository,
            clock=lambda: NOW,
        )

        assert paused.status is MissionStatus.paused
        assert paused.scheduled_at is mission.scheduled_at
        assert paused.execution_log[-1].type == "mission_paused"
        assert paused.execution_log[-1].metadata == {
            "previous_status": status.value
        }

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("scheduled_at", "expected_status"),
    [
        (None, MissionStatus.created),
        (SCHEDULED_AT, MissionStatus.waiting),
    ],
)
def test_resume_mission_restores_planning_state(
    scheduled_at: datetime | None,
    expected_status: MissionStatus,
) -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()
        mission = make_mission(
            status=MissionStatus.paused,
            scheduled_at=scheduled_at,
        )
        await repository.create(mission)

        resumed = await resume_mission(
            mission.id,
            repository,
            clock=lambda: NOW,
        )

        assert resumed.status is expected_status
        assert resumed.execution_log[-1].type == "mission_resumed"
        assert resumed.execution_log[-1].metadata == {
            "resumed_status": expected_status.value
        }

    asyncio.run(scenario())


def test_pause_and_resume_reject_invalid_states() -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()
        running = make_mission(status=MissionStatus.running)
        created = make_mission()
        await repository.create(running)
        await repository.create(created)

        with pytest.raises(MissionPauseNotAllowedError):
            await pause_mission(running.id, repository)
        with pytest.raises(MissionResumeNotAllowedError):
            await resume_mission(created.id, repository)

    asyncio.run(scenario())

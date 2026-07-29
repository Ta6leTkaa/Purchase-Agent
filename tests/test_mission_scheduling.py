import asyncio
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.mission import Mission, MissionStatus, MissionType, TrainConstraints
from app.services.mission_scheduling import (
    InvalidMissionScheduleError,
    MissionSchedulingNotAllowedError,
    schedule_mission,
)
from app.storage.memory import InMemoryMissionRepository


def make_mission(status: MissionStatus = MissionStatus.created) -> Mission:
    return Mission(
        id=uuid4(),
        type=MissionType.TRAIN_TICKET,
        title="Scheduled train",
        status=status,
        participant_ids=[uuid4()],
        provider="mock_train",
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Saint Petersburg",
            travel_date=date(2026, 8, 1),
            passengers_count=1,
        ),
    )


def test_schedule_created_mission_moves_it_to_waiting_and_records_event() -> None:
    repository = InMemoryMissionRepository()
    mission = make_mission()
    asyncio.run(repository.create(mission))
    now = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
    scheduled_at = now + timedelta(hours=1)

    scheduled = asyncio.run(
        schedule_mission(
            mission.id,
            scheduled_at,
            repository,
            current_time=now,
        )
    )

    assert scheduled.status is MissionStatus.waiting
    assert scheduled.scheduled_at == scheduled_at
    assert scheduled.execution_log[-1].type == "mission_scheduled"


def test_reschedule_waiting_mission_is_idempotent_for_same_time() -> None:
    repository = InMemoryMissionRepository()
    now = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
    scheduled_at = now + timedelta(hours=1)
    mission = make_mission(MissionStatus.waiting)
    mission.scheduled_at = scheduled_at
    asyncio.run(repository.create(mission))

    result = asyncio.run(
        schedule_mission(
            mission.id,
            scheduled_at,
            repository,
            current_time=now,
        )
    )

    assert result.execution_log == []


def test_unschedule_waiting_mission_moves_it_to_created_and_records_event() -> None:
    repository = InMemoryMissionRepository()
    now = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
    mission = make_mission(MissionStatus.waiting)
    mission.scheduled_at = now + timedelta(hours=1)
    asyncio.run(repository.create(mission))

    unscheduled = asyncio.run(
        schedule_mission(
            mission.id,
            None,
            repository,
            current_time=now,
        )
    )

    assert unscheduled.status is MissionStatus.created
    assert unscheduled.scheduled_at is None
    assert unscheduled.execution_log[-1].type == "mission_unscheduled"


def test_unschedule_created_mission_is_a_noop() -> None:
    repository = InMemoryMissionRepository()
    mission = make_mission()
    asyncio.run(repository.create(mission))
    now = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)

    result = asyncio.run(
        schedule_mission(
            mission.id,
            None,
            repository,
            current_time=now,
        )
    )

    assert result.status is MissionStatus.created
    assert result.execution_log == []


def test_schedule_terminal_mission_is_rejected() -> None:
    repository = InMemoryMissionRepository()
    mission = make_mission(MissionStatus.completed)
    asyncio.run(repository.create(mission))
    now = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)

    with pytest.raises(MissionSchedulingNotAllowedError):
        asyncio.run(
            schedule_mission(
                mission.id,
                now + timedelta(hours=1),
                repository,
                current_time=now,
            )
        )


def test_schedule_rejects_non_future_time() -> None:
    repository = InMemoryMissionRepository()
    mission = make_mission()
    asyncio.run(repository.create(mission))
    now = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)

    with pytest.raises(InvalidMissionScheduleError):
        asyncio.run(
            schedule_mission(
                mission.id,
                now,
                repository,
                current_time=now,
            )
        )

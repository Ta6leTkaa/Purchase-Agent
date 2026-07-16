import asyncio
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.domain.execution import ExecutionEvent
from app.domain.mission import (
    Mission,
    MissionStatus,
    MissionType,
    TrainConstraints,
)
from app.repositories.mission import InvalidRepositoryTimeError
from app.storage.memory import InMemoryMissionRepository


def test_list_due_returns_waiting_mission_with_due_scheduled_at() -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()
        current_time = aware_datetime()
        due_mission = make_mission(
            status=MissionStatus.waiting,
            scheduled_at=current_time,
        )
        await repository.create(due_mission)

        due_missions = await repository.list_due(current_time)

        assert due_missions == [due_mission]

    asyncio.run(scenario())


def test_list_due_filters_future_created_and_unscheduled_missions() -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()
        current_time = aware_datetime()
        due_mission = make_mission(
            status=MissionStatus.waiting,
            scheduled_at=current_time,
        )
        future_mission = make_mission(
            status=MissionStatus.waiting,
            scheduled_at=current_time + timedelta(minutes=1),
        )
        created_mission = make_mission(
            status=MissionStatus.created,
            scheduled_at=current_time,
        )
        unscheduled_mission = make_mission(status=MissionStatus.waiting)
        for mission in [
            due_mission,
            future_mission,
            created_mission,
            unscheduled_mission,
        ]:
            await repository.create(mission)

        due_missions = await repository.list_due(current_time)

        assert due_missions == [due_mission]

    asyncio.run(scenario())


def test_list_due_sorts_by_scheduled_at_and_applies_limit() -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()
        current_time = aware_datetime()
        later_mission = make_mission(
            status=MissionStatus.waiting,
            scheduled_at=current_time - timedelta(minutes=1),
        )
        earlier_mission = make_mission(
            status=MissionStatus.waiting,
            scheduled_at=current_time - timedelta(minutes=2),
        )
        await repository.create(later_mission)
        await repository.create(earlier_mission)

        due_missions = await repository.list_due(current_time, limit=1)

        assert due_missions == [earlier_mission]

    asyncio.run(scenario())


def test_list_due_rejects_naive_current_time() -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()

        with pytest.raises(InvalidRepositoryTimeError):
            await repository.list_due(datetime(2026, 8, 1, 10, 0))

    asyncio.run(scenario())


def test_list_due_rejects_non_positive_limit() -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()

        with pytest.raises(ValueError):
            await repository.list_due(aware_datetime(), limit=0)

    asyncio.run(scenario())


def test_list_due_does_not_change_status_or_execution_log() -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()
        current_time = aware_datetime()
        mission = make_mission(
            status=MissionStatus.waiting,
            scheduled_at=current_time,
            execution_log=[
                ExecutionEvent(
                    timestamp=current_time,
                    type="mission_scheduled",
                    message="Mission scheduled.",
                )
            ],
        )
        await repository.create(mission)

        await repository.list_due(current_time)
        stored_mission = await repository.get(mission.id)

        assert stored_mission is not None
        assert stored_mission.status is MissionStatus.waiting
        assert stored_mission.execution_log == mission.execution_log

    asyncio.run(scenario())


def make_mission(
    status: MissionStatus,
    scheduled_at: datetime | None = None,
    execution_log: list[ExecutionEvent] | None = None,
) -> Mission:
    return Mission(
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
        scheduled_at=scheduled_at,
        execution_log=execution_log or [],
    )


def aware_datetime() -> datetime:
    return datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)

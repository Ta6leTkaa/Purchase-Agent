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
            last_event_sequence=1,
            execution_log=[
                ExecutionEvent(
                    sequence=1,
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


def test_claim_due_returns_due_waiting_missions_as_processing() -> None:
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
        for mission in [due_mission, future_mission, created_mission]:
            await repository.create(mission)

        claimed_missions = await repository.claim_due(current_time)

        assert [mission.id for mission in claimed_missions] == [
            due_mission.id
        ]
        assert claimed_missions[0].status is MissionStatus.processing
        assert claimed_missions[0].claimed_at == current_time
        assert claimed_missions[0].execution_attempts == 1
        assert future_mission.execution_attempts == 0
        assert created_mission.execution_attempts == 0
        assert future_mission.claimed_at is None
        assert created_mission.claimed_at is None

    asyncio.run(scenario())


def test_claim_due_does_not_return_same_mission_twice() -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()
        current_time = aware_datetime()
        mission = make_mission(
            status=MissionStatus.waiting,
            scheduled_at=current_time,
        )
        await repository.create(mission)

        first_claim = await repository.claim_due(current_time)
        second_claim = await repository.claim_due(
            current_time + timedelta(minutes=5)
        )
        stored_mission = await repository.get(mission.id)

        assert [mission.id for mission in first_claim] == [mission.id]
        assert second_claim == []
        assert first_claim[0].claimed_at == current_time
        assert stored_mission is not None
        assert stored_mission.claimed_at == current_time
        assert stored_mission.execution_attempts == 1

    asyncio.run(scenario())


def test_claim_due_concurrent_calls_do_not_overlap() -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()
        current_time = aware_datetime()
        missions = [
            make_mission(
                status=MissionStatus.waiting,
                scheduled_at=current_time + timedelta(seconds=index),
            )
            for index in range(4)
        ]
        for mission in missions:
            await repository.create(mission)

        first_claim, second_claim = await asyncio.gather(
            repository.claim_due(current_time + timedelta(seconds=10), limit=2),
            repository.claim_due(current_time + timedelta(seconds=10), limit=2),
        )
        claimed_ids = [
            mission.id
            for mission in [*first_claim, *second_claim]
        ]

        assert len(claimed_ids) == 4
        assert len(set(claimed_ids)) == 4
        assert set(claimed_ids) == {mission.id for mission in missions}
        assert all(
            mission.status is MissionStatus.processing
            for mission in [*first_claim, *second_claim]
        )
        assert all(
            mission.claimed_at == current_time + timedelta(seconds=10)
            for mission in [*first_claim, *second_claim]
        )
        assert all(
            mission.execution_attempts == 1
            for mission in [*first_claim, *second_claim]
        )

    asyncio.run(scenario())


def test_claim_due_sorts_by_scheduled_at_and_applies_limit() -> None:
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

        claimed_missions = await repository.claim_due(current_time, limit=1)

        assert [mission.id for mission in claimed_missions] == [
            earlier_mission.id
        ]
        assert claimed_missions[0].claimed_at == current_time
        assert claimed_missions[0].execution_attempts == 1

    asyncio.run(scenario())


def test_claim_after_recovery_increments_execution_attempts_again() -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()
        current_time = aware_datetime()
        mission = make_mission(
            status=MissionStatus.waiting,
            scheduled_at=current_time,
        )
        await repository.create(mission)

        first_claim = await repository.claim_due(current_time)
        recovered_missions = await repository.recover_stale_processing(
            current_time + timedelta(minutes=16),
            timedelta(minutes=15),
        )
        second_claim = await repository.claim_due(
            current_time + timedelta(minutes=16)
        )

        assert first_claim[0].execution_attempts == 1
        assert recovered_missions[0].execution_attempts == 1
        assert second_claim[0].execution_attempts == 2

    asyncio.run(scenario())


def test_claim_due_skips_missions_with_exhausted_attempts() -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()
        current_time = aware_datetime()
        available_mission = make_mission(
            status=MissionStatus.waiting,
            scheduled_at=current_time,
        )
        exhausted_mission = make_mission(
            status=MissionStatus.waiting,
            scheduled_at=current_time,
        )
        exhausted_mission.execution_attempts = 2
        exhausted_mission.max_execution_attempts = 2
        await repository.create(available_mission)
        await repository.create(exhausted_mission)

        claimed_missions = await repository.claim_due(current_time)

        assert [mission.id for mission in claimed_missions] == [
            available_mission.id
        ]
        assert claimed_missions[0].execution_attempts == 1
        assert exhausted_mission.status is MissionStatus.waiting
        assert exhausted_mission.claimed_at is None
        assert exhausted_mission.execution_attempts == 2

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

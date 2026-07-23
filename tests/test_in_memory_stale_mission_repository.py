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


def test_list_stale_processing_filters_orders_limits_and_is_read_only() -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()
        current_time = aware_datetime()
        oldest_mission = make_mission(
            claimed_at=current_time - timedelta(minutes=30)
        )
        boundary_mission = make_mission(
            claimed_at=current_time - timedelta(minutes=15)
        )
        fresh_mission = make_mission(
            claimed_at=current_time - timedelta(minutes=14)
        )
        waiting_mission = make_mission(
            status=MissionStatus.waiting,
            claimed_at=None,
        )
        failed_mission = make_mission(
            status=MissionStatus.failed,
            claimed_at=None,
        )
        completed_mission = make_mission(
            status=MissionStatus.completed,
            claimed_at=None,
        )
        confirmation_mission = make_mission(
            status=MissionStatus.requires_confirmation,
            claimed_at=None,
        )
        legacy_processing_mission = make_legacy_processing_mission()
        missions = [
            boundary_mission,
            fresh_mission,
            waiting_mission,
            failed_mission,
            completed_mission,
            confirmation_mission,
            legacy_processing_mission,
            oldest_mission,
        ]
        for mission in missions:
            await repository.create(mission)

        stale_missions = await repository.list_stale_processing(
            current_time,
            timedelta(minutes=15),
        )
        limited_missions = await repository.list_stale_processing(
            current_time,
            timedelta(minutes=15),
            limit=1,
        )

        assert [mission.id for mission in stale_missions] == [
            oldest_mission.id,
            boundary_mission.id,
        ]
        assert [mission.id for mission in limited_missions] == [
            oldest_mission.id
        ]
        assert oldest_mission.status is MissionStatus.processing
        assert oldest_mission.claimed_at == current_time - timedelta(minutes=30)
        assert oldest_mission.execution_log == [make_event()]
        assert legacy_processing_mission.claimed_at is None

    asyncio.run(scenario())


def test_list_stale_processing_rejects_invalid_arguments() -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()

        with pytest.raises(InvalidRepositoryTimeError):
            await repository.list_stale_processing(
                datetime(2026, 8, 1, 10, 0),
                timedelta(minutes=15),
            )
        with pytest.raises(ValueError, match="claim_timeout"):
            await repository.list_stale_processing(
                aware_datetime(),
                timedelta(0),
            )
        with pytest.raises(ValueError, match="claim_timeout"):
            await repository.list_stale_processing(
                aware_datetime(),
                timedelta(minutes=-1),
            )
        with pytest.raises(ValueError, match="limit"):
            await repository.list_stale_processing(
                aware_datetime(),
                timedelta(minutes=15),
                limit=0,
            )

    asyncio.run(scenario())


def test_recover_stale_processing_recovers_only_stale_missions() -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()
        current_time = aware_datetime()
        oldest_mission = make_mission(
            claimed_at=current_time - timedelta(minutes=30)
        )
        boundary_mission = make_mission(
            claimed_at=current_time - timedelta(minutes=15)
        )
        fresh_mission = make_mission(
            claimed_at=current_time - timedelta(minutes=14)
        )
        waiting_mission = make_mission(
            status=MissionStatus.waiting,
            claimed_at=None,
        )
        failed_mission = make_mission(
            status=MissionStatus.failed,
            claimed_at=None,
        )
        completed_mission = make_mission(
            status=MissionStatus.completed,
            claimed_at=None,
        )
        confirmation_mission = make_mission(
            status=MissionStatus.requires_confirmation,
            claimed_at=None,
        )
        legacy_processing_mission = make_legacy_processing_mission()
        original_scheduled_at = current_time - timedelta(hours=1)
        oldest_mission.scheduled_at = original_scheduled_at
        for mission in [
            boundary_mission,
            fresh_mission,
            waiting_mission,
            failed_mission,
            completed_mission,
            confirmation_mission,
            legacy_processing_mission,
            oldest_mission,
        ]:
            await repository.create(mission)

        recovered_missions = await repository.recover_stale_processing(
            current_time,
            timedelta(minutes=15),
        )
        second_recovery = await repository.recover_stale_processing(
            current_time,
            timedelta(minutes=15),
        )

        assert [mission.id for mission in recovered_missions] == [
            oldest_mission.id,
            boundary_mission.id,
        ]
        assert second_recovery == []
        for mission in recovered_missions:
            assert mission.status is MissionStatus.waiting
            assert mission.claimed_at is None
            assert mission.execution_log[-1].type == "claim_recovered"
            assert mission.execution_log[-1].timestamp == current_time
            assert mission.execution_log[-1].metadata["previous_claimed_at"]
        assert oldest_mission.scheduled_at == original_scheduled_at
        assert fresh_mission.status is MissionStatus.processing
        assert fresh_mission.claimed_at == current_time - timedelta(minutes=14)
        assert waiting_mission.status is MissionStatus.waiting
        assert failed_mission.status is MissionStatus.failed
        assert completed_mission.status is MissionStatus.completed
        assert confirmation_mission.status is MissionStatus.requires_confirmation
        assert legacy_processing_mission.status is MissionStatus.processing
        assert legacy_processing_mission.claimed_at is None

    asyncio.run(scenario())


def test_recover_stale_processing_honors_limit_and_concurrency() -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()
        current_time = aware_datetime()
        missions = [
            make_mission(
                claimed_at=current_time - timedelta(minutes=30 - index)
            )
            for index in range(4)
        ]
        for mission in missions:
            await repository.create(mission)

        first_recovery, second_recovery = await asyncio.gather(
            repository.recover_stale_processing(
                current_time,
                timedelta(minutes=15),
                limit=2,
            ),
            repository.recover_stale_processing(
                current_time,
                timedelta(minutes=15),
                limit=2,
            ),
        )
        recovered_ids = [
            mission.id
            for mission in [*first_recovery, *second_recovery]
        ]

        assert len(recovered_ids) == 4
        assert len(set(recovered_ids)) == 4
        assert recovered_ids[:2] == [missions[0].id, missions[1].id]
        assert all(
            len(mission.execution_log) == 2
            for mission in [*first_recovery, *second_recovery]
        )

    asyncio.run(scenario())


def test_recover_stale_processing_rejects_invalid_arguments() -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()

        with pytest.raises(InvalidRepositoryTimeError):
            await repository.recover_stale_processing(
                datetime(2026, 8, 1, 10, 0),
                timedelta(minutes=15),
            )
        with pytest.raises(ValueError, match="claim_timeout"):
            await repository.recover_stale_processing(
                aware_datetime(),
                timedelta(0),
            )
        with pytest.raises(ValueError, match="limit"):
            await repository.recover_stale_processing(
                aware_datetime(),
                timedelta(minutes=15),
                limit=0,
            )

    asyncio.run(scenario())


def test_recover_stale_processing_fails_mission_with_exhausted_attempts() -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()
        mission = make_mission(
            claimed_at=aware_datetime() - timedelta(minutes=15)
        )
        mission.execution_attempts = 2
        mission.max_execution_attempts = 2
        await repository.create(mission)

        recovered_missions = await repository.recover_stale_processing(
            aware_datetime(),
            timedelta(minutes=15),
        )

        assert recovered_missions == [mission]
        assert mission.status is MissionStatus.failed
        assert mission.claimed_at is None
        assert mission.execution_attempts == 2
        assert mission.execution_log[-1].metadata["attempts_exhausted"] is True

    asyncio.run(scenario())


def make_mission(
    status: MissionStatus = MissionStatus.processing,
    claimed_at: datetime | None = None,
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
        claimed_at=claimed_at,
        last_event_sequence=1,
        execution_log=[make_event()],
    )


def make_legacy_processing_mission() -> Mission:
    mission = make_mission(claimed_at=aware_datetime())
    return Mission.model_construct(
        **{**mission.model_dump(), "claimed_at": None}
    )


def make_event() -> ExecutionEvent:
    return ExecutionEvent(
        sequence=1,
        timestamp=aware_datetime(),
        type="mission_started",
        message="Mission started.",
    )


def aware_datetime() -> datetime:
    return datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)

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
        execution_log=[make_event()],
    )


def make_legacy_processing_mission() -> Mission:
    mission = make_mission(claimed_at=aware_datetime())
    return Mission.model_construct(
        **{**mission.model_dump(), "claimed_at": None}
    )


def make_event() -> ExecutionEvent:
    return ExecutionEvent(
        timestamp=aware_datetime(),
        type="mission_started",
        message="Mission started.",
    )


def aware_datetime() -> datetime:
    return datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)

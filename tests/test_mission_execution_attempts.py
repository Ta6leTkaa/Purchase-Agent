import asyncio
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domain.execution_attempt import (
    MissionExecutionAttempt,
    MissionExecutionAttemptStatus,
)
from app.domain.mission import (
    Mission,
    MissionStatus,
    TrainConstraints,
    TrainTicketMissionPayload,
)
from app.storage.memory import InMemoryMissionRepository


def test_execution_attempt_is_immutable_and_requires_a_terminal_time() -> None:
    claimed_at = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
    attempt = MissionExecutionAttempt(
        mission_id=uuid4(),
        attempt_number=1,
        status=MissionExecutionAttemptStatus.processing,
        claimed_at=claimed_at,
    )

    with pytest.raises(ValidationError):
        attempt.attempt_number = 2

    with pytest.raises(ValueError, match="finished attempt"):
        MissionExecutionAttempt(
            mission_id=uuid4(),
            attempt_number=1,
            status=MissionExecutionAttemptStatus.completed,
            claimed_at=claimed_at,
        )


def test_claim_creates_and_completion_closes_execution_attempt() -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()
        current_time = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
        mission = make_due_mission(current_time)
        await repository.create(mission)

        claimed = (await repository.claim_due(current_time))[0]
        attempts = await repository.list_execution_attempts(mission.id)

        assert len(attempts) == 1
        assert attempts[0].mission_id == mission.id
        assert attempts[0].attempt_number == 1
        assert attempts[0].status is MissionExecutionAttemptStatus.processing
        assert attempts[0].claimed_at == current_time

        claimed.resolved_provider_id = "mock_train"
        await repository.update(claimed)
        claimed.reservation_id = "mock-reservation-123"
        claimed.status = MissionStatus.completed
        claimed.claimed_at = None
        claimed.record_event(
            timestamp=current_time + timedelta(minutes=1),
            event_type="mission_completed",
            message="Mission completed.",
        )
        await repository.update(claimed)

        attempts = await repository.list_execution_attempts(mission.id)
        assert attempts[0].status is MissionExecutionAttemptStatus.completed
        assert attempts[0].finished_at == current_time + timedelta(minutes=1)
        assert attempts[0].resolved_provider_id == "mock_train"
        assert attempts[0].reservation_id == "mock-reservation-123"

    asyncio.run(scenario())


def test_stale_recovery_closes_attempt_without_changing_attempt_number() -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()
        current_time = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
        mission = make_due_mission(current_time)
        await repository.create(mission)
        await repository.claim_due(current_time)

        recovered = await repository.recover_stale_processing(
            current_time + timedelta(minutes=16),
            claim_timeout=timedelta(minutes=15),
        )
        attempts = await repository.list_execution_attempts(mission.id)

        assert recovered[0].execution_attempts == 1
        assert attempts[0].attempt_number == 1
        assert attempts[0].status is MissionExecutionAttemptStatus.recovered
        assert attempts[0].finished_at == current_time + timedelta(minutes=16)

    asyncio.run(scenario())


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

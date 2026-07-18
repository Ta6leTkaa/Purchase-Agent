import asyncio
from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.adapters.mock_train import MockTrainAdapter
from app.domain.identity import Identity
from app.domain.mission import (
    FallbackRules,
    Mission,
    MissionStatus,
    MissionType,
    TrainConstraints,
)
from app.domain.provider import ProviderOption
from app.services import mission_engine
from app.services.due_mission_processor import process_due_missions
from app.storage.memory import InMemoryIdentityRepository, InMemoryMissionRepository


def test_due_mission_is_started_and_future_mission_is_skipped() -> None:
    async def scenario() -> None:
        identity_repository = InMemoryIdentityRepository()
        mission_repository = InMemoryMissionRepository()
        current_time = aware_datetime()
        identities = [
            await identity_repository.create(make_identity())
            for _ in range(4)
        ]
        due_mission = make_mission(
            [identity.id for identity in identities],
            scheduled_at=current_time,
        )
        future_mission = make_mission(
            [identity.id for identity in identities],
            scheduled_at=current_time + timedelta(minutes=1),
        )
        await mission_repository.create(due_mission)
        await mission_repository.create(future_mission)

        result = await process_due_missions(
            mission_repository,
            identity_repository,
            current_time,
        )
        stored_due_mission = await mission_repository.get(due_mission.id)
        stored_future_mission = await mission_repository.get(future_mission.id)

        assert result.processed_count == 1
        assert result.succeeded_mission_ids == [due_mission.id]
        assert result.failed_mission_ids == []
        assert stored_due_mission is not None
        assert stored_due_mission.status is MissionStatus.requires_confirmation
        assert stored_due_mission.status is not MissionStatus.processing
        assert stored_due_mission.claimed_at is None
        assert stored_future_mission is not None
        assert stored_future_mission.status is MissionStatus.waiting
        assert stored_future_mission.claimed_at is None
        assert stored_future_mission.execution_log == []

    asyncio.run(scenario())


def test_multiple_due_missions_are_processed_in_scheduled_order() -> None:
    async def scenario() -> None:
        identity_repository = InMemoryIdentityRepository()
        mission_repository = InMemoryMissionRepository()
        current_time = aware_datetime()
        identities = [
            await identity_repository.create(make_identity())
            for _ in range(4)
        ]
        later_mission = make_mission(
            [identity.id for identity in identities],
            scheduled_at=current_time - timedelta(minutes=1),
        )
        earlier_mission = make_mission(
            [identity.id for identity in identities],
            scheduled_at=current_time - timedelta(minutes=2),
        )
        await mission_repository.create(later_mission)
        await mission_repository.create(earlier_mission)

        result = await process_due_missions(
            mission_repository,
            identity_repository,
            current_time,
        )

        assert result.processed_count == 2
        assert result.succeeded_mission_ids == [
            earlier_mission.id,
            later_mission.id,
        ]
        assert result.failed_mission_ids == []

    asyncio.run(scenario())


def test_failed_mission_does_not_stop_next_due_mission() -> None:
    async def scenario() -> None:
        identity_repository = InMemoryIdentityRepository()
        mission_repository = InMemoryMissionRepository()
        current_time = aware_datetime()
        identities = [
            await identity_repository.create(make_identity())
            for _ in range(4)
        ]
        missing_participant_mission = make_mission(
            [uuid4()],
            scheduled_at=current_time - timedelta(minutes=2),
        )
        successful_mission = make_mission(
            [identity.id for identity in identities],
            scheduled_at=current_time - timedelta(minutes=1),
        )
        await mission_repository.create(missing_participant_mission)
        await mission_repository.create(successful_mission)

        result = await process_due_missions(
            mission_repository,
            identity_repository,
            current_time,
        )
        stored_failed_mission = await mission_repository.get(
            missing_participant_mission.id
        )
        stored_successful_mission = await mission_repository.get(
            successful_mission.id
        )

        assert result.processed_count == 2
        assert result.failed_mission_ids == [missing_participant_mission.id]
        assert result.succeeded_mission_ids == [successful_mission.id]
        assert result.errors == {}
        assert stored_failed_mission is not None
        assert stored_failed_mission.status is MissionStatus.failed
        assert stored_failed_mission.claimed_at is None
        assert stored_successful_mission is not None
        assert stored_successful_mission.status is (
            MissionStatus.requires_confirmation
        )
        assert stored_successful_mission.claimed_at is None

    asyncio.run(scenario())


def test_adapter_receives_processing_mission_with_claimed_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        adapter = CapturingMockTrainAdapter()
        monkeypatch.setattr(
            mission_engine,
            "get_adapter",
            lambda provider_id: adapter,
        )
        identity_repository = InMemoryIdentityRepository()
        mission_repository = InMemoryMissionRepository()
        current_time = aware_datetime()
        identities = [
            await identity_repository.create(make_identity())
            for _ in range(4)
        ]
        mission = make_mission(
            [identity.id for identity in identities],
            scheduled_at=current_time,
        )
        await mission_repository.create(mission)

        await process_due_missions(
            mission_repository,
            identity_repository,
            current_time,
        )

        assert adapter.seen_status is MissionStatus.processing
        assert adapter.seen_claimed_at == current_time

    asyncio.run(scenario())


def test_exception_for_one_mission_does_not_stop_next_due_mission() -> None:
    async def scenario() -> None:
        identity_repository = InMemoryIdentityRepository()
        mission_repository = InMemoryMissionRepository()
        current_time = aware_datetime()
        identities = [
            await identity_repository.create(make_identity())
            for _ in range(4)
        ]
        broken_mission = make_mission(
            [identity.id for identity in identities],
            scheduled_at=current_time - timedelta(minutes=2),
            provider="unknown_provider",
        )
        successful_mission = make_mission(
            [identity.id for identity in identities],
            scheduled_at=current_time - timedelta(minutes=1),
        )
        await mission_repository.create(broken_mission)
        await mission_repository.create(successful_mission)

        result = await process_due_missions(
            mission_repository,
            identity_repository,
            current_time,
        )

        assert result.processed_count == 2
        assert result.failed_mission_ids == [broken_mission.id]
        assert result.succeeded_mission_ids == [successful_mission.id]
        assert broken_mission.id in result.errors
        assert "unknown_provider" in result.errors[broken_mission.id]
        stored_broken_mission = await mission_repository.get(broken_mission.id)
        assert stored_broken_mission is not None
        assert stored_broken_mission.status is MissionStatus.failed
        assert stored_broken_mission.claimed_at is None
        assert stored_broken_mission.execution_log[-1].type == (
            "mission_processing_failed"
        )

    asyncio.run(scenario())


def test_second_processing_cycle_does_not_reprocess_same_mission() -> None:
    async def scenario() -> None:
        identity_repository = InMemoryIdentityRepository()
        mission_repository = InMemoryMissionRepository()
        current_time = aware_datetime()
        identities = [
            await identity_repository.create(make_identity())
            for _ in range(4)
        ]
        mission = make_mission(
            [identity.id for identity in identities],
            scheduled_at=current_time,
        )
        await mission_repository.create(mission)

        first_result = await process_due_missions(
            mission_repository,
            identity_repository,
            current_time,
        )
        second_result = await process_due_missions(
            mission_repository,
            identity_repository,
            current_time,
        )
        stored_mission = await mission_repository.get(mission.id)

        assert first_result.processed_count == 1
        assert second_result.processed_count == 0
        assert stored_mission is not None
        assert stored_mission.status is MissionStatus.requires_confirmation
        assert stored_mission.claimed_at is None

    asyncio.run(scenario())


def test_empty_result_when_no_due_missions_exist() -> None:
    async def scenario() -> None:
        identity_repository = InMemoryIdentityRepository()
        mission_repository = InMemoryMissionRepository()

        result = await process_due_missions(
            mission_repository,
            identity_repository,
            aware_datetime(),
        )

        assert result.processed_count == 0
        assert result.succeeded_mission_ids == []
        assert result.failed_mission_ids == []
        assert result.errors == {}

    asyncio.run(scenario())


def make_identity() -> Identity:
    return Identity(
        id=uuid4(),
        display_name="Ivan Petrov",
        first_name="Ivan",
        last_name="Petrov",
        birth_date=date(1990, 1, 1),
    )


def make_mission(
    participant_ids: list[UUID],
    scheduled_at: datetime,
    provider: str = "mock_train",
) -> Mission:
    return Mission(
        id=uuid4(),
        type=MissionType.train_trip,
        title="Moscow to Saint Petersburg",
        status=MissionStatus.waiting,
        participant_ids=participant_ids,
        provider=provider,
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Saint Petersburg",
            travel_date=date(2026, 8, 1),
            passengers_count=len(participant_ids),
            must_be_same_compartment=True,
            min_lower_berths=2,
            max_total_price=30000,
            avoid_toilet=True,
        ),
        fallback_rules=FallbackRules(allow_adjacent_compartments=True),
        scheduled_at=scheduled_at,
    )


def aware_datetime() -> datetime:
    return datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)


class CapturingMockTrainAdapter(MockTrainAdapter):
    def __init__(self) -> None:
        self.seen_status: MissionStatus | None = None
        self.seen_claimed_at: datetime | None = None

    async def search_options(
        self,
        mission: Mission,
        identities: list[Identity],
    ) -> list[ProviderOption]:
        self.seen_status = mission.status
        self.seen_claimed_at = mission.claimed_at
        return await super().search_options(mission, identities)

import asyncio
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

from app.adapters.mock_train import MockTrainAdapter
from app.adapters.registry import ProviderRegistry
from app.domain.execution_attempt import MissionExecutionAttemptStatus
from app.domain.identity import Identity
from app.domain.mission import (
    FallbackRules,
    Mission,
    MissionStatus,
    MissionType,
    TrainConstraints,
)
from app.domain.provider import ProviderOption
from app.services.due_mission_processor import process_due_missions
from app.services.provider_errors import ProviderOperationError
from app.services.provider_resolver import ProviderResolver
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


def test_adapter_receives_processing_mission_with_claimed_at() -> None:
    async def scenario() -> None:
        adapter = CapturingMockTrainAdapter()
        resolver = ProviderResolver(ProviderRegistry([adapter]))
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
            provider_resolver=resolver,
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
            provider_id="unknown_provider",
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


def test_typed_provider_failure_closes_attempt_and_schedules_retry() -> None:
    class FailingSearchAdapter(MockTrainAdapter):
        async def search_options(
            self,
            mission: Mission,
            identities: list[Identity],
        ) -> list[ProviderOption]:
            raise ProviderOperationError(
                provider_id=self.provider_id,
                operation="search",
            )

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
        resolver = ProviderResolver(ProviderRegistry([FailingSearchAdapter()]))

        result = await process_due_missions(
            mission_repository,
            identity_repository,
            current_time,
            provider_resolver=resolver,
        )
        stored_mission = await mission_repository.get(mission.id)
        attempts = await mission_repository.list_execution_attempts(mission.id)

        assert result.processed_count == 1
        assert result.succeeded_mission_ids == []
        assert result.failed_mission_ids == []
        assert result.retry_scheduled_mission_ids == [mission.id]
        assert result.errors == {}
        assert stored_mission is not None
        assert stored_mission.status is MissionStatus.waiting
        assert stored_mission.claimed_at is None
        assert stored_mission.scheduled_at == current_time + timedelta(seconds=30)
        assert [event.type for event in stored_mission.execution_log[-2:]] == [
            "provider_operation_failed",
            "mission_retry_scheduled",
        ]
        assert stored_mission.execution_log[-1].metadata["trigger"] == (
            "automatic"
        )
        assert len(attempts) == 1
        assert attempts[0].status is MissionExecutionAttemptStatus.failed

    asyncio.run(scenario())


def test_retry_backoff_grows_until_attempts_are_exhausted() -> None:
    class FailingSearchAdapter(MockTrainAdapter):
        async def search_options(
            self,
            mission: Mission,
            identities: list[Identity],
        ) -> list[ProviderOption]:
            raise ProviderOperationError(
                provider_id=self.provider_id,
                operation="search",
            )

    async def scenario() -> None:
        identity_repository = InMemoryIdentityRepository()
        mission_repository = InMemoryMissionRepository()
        identities = [
            await identity_repository.create(make_identity())
            for _ in range(4)
        ]
        mission = make_mission(
            [identity.id for identity in identities],
            scheduled_at=aware_datetime(),
        )
        await mission_repository.create(mission)
        resolver = ProviderResolver(ProviderRegistry([FailingSearchAdapter()]))

        first = await process_due_missions(
            mission_repository,
            identity_repository,
            aware_datetime(),
            provider_resolver=resolver,
        )
        second_time = aware_datetime() + timedelta(seconds=30)
        second = await process_due_missions(
            mission_repository,
            identity_repository,
            second_time,
            provider_resolver=resolver,
        )
        third_time = second_time + timedelta(seconds=60)
        third = await process_due_missions(
            mission_repository,
            identity_repository,
            third_time,
            provider_resolver=resolver,
        )
        stored = await mission_repository.get(mission.id)
        attempts = await mission_repository.list_execution_attempts(mission.id)

        assert first.retry_scheduled_mission_ids == [mission.id]
        assert second.retry_scheduled_mission_ids == [mission.id]
        assert third.retry_scheduled_mission_ids == []
        assert third.failed_mission_ids == [mission.id]
        assert stored is not None
        assert stored.status is MissionStatus.failed
        assert stored.execution_attempts == 3
        assert len(attempts) == 3
        retry_events = [
            event
            for event in stored.execution_log
            if event.type == "mission_retry_scheduled"
        ]
        assert [event.metadata["retry_at"] for event in retry_events] == [
            second_time.isoformat(),
            third_time.isoformat(),
        ]

    asyncio.run(scenario())


def test_non_retryable_provider_failure_remains_failed() -> None:
    class NonRetryableSearchAdapter(MockTrainAdapter):
        async def search_options(
            self,
            mission: Mission,
            identities: list[Identity],
        ) -> list[ProviderOption]:
            raise ProviderOperationError(
                provider_id=self.provider_id,
                operation="search",
                retryable=False,
            )

    async def scenario() -> None:
        identity_repository = InMemoryIdentityRepository()
        mission_repository = InMemoryMissionRepository()
        identities = [
            await identity_repository.create(make_identity())
            for _ in range(4)
        ]
        mission = make_mission(
            [identity.id for identity in identities],
            scheduled_at=aware_datetime(),
        )
        await mission_repository.create(mission)

        result = await process_due_missions(
            mission_repository,
            identity_repository,
            aware_datetime(),
            provider_resolver=ProviderResolver(
                ProviderRegistry([NonRetryableSearchAdapter()])
            ),
        )

        assert result.failed_mission_ids == [mission.id]
        assert result.retry_scheduled_mission_ids == []
        assert mission.status is MissionStatus.failed
        assert mission.execution_log[-2].metadata["retryable"] is False
        assert mission.execution_log[-1].type == "mission_failed"

    asyncio.run(scenario())


def test_no_valid_option_is_rescheduled_for_monitoring() -> None:
    class EmptySearchAdapter(MockTrainAdapter):
        async def search_options(
            self,
            mission: Mission,
            identities: list[Identity],
        ) -> list[ProviderOption]:
            return []

    async def scenario() -> None:
        identity_repository = InMemoryIdentityRepository()
        mission_repository = InMemoryMissionRepository()
        current_time = aware_datetime()
        identity = await identity_repository.create(make_identity())
        mission = make_mission(
            [identity.id],
            scheduled_at=current_time,
        )
        await mission_repository.create(mission)

        result = await process_due_missions(
            mission_repository,
            identity_repository,
            current_time,
            provider_resolver=ProviderResolver(
                ProviderRegistry([EmptySearchAdapter()])
            ),
        )

        assert result.failed_mission_ids == []
        assert result.retry_scheduled_mission_ids == [mission.id]
        assert mission.status is MissionStatus.waiting
        assert mission.scheduled_at == current_time + timedelta(seconds=30)
        assert [event.type for event in mission.execution_log[-2:]] == [
            "no_valid_option_found",
            "mission_retry_scheduled",
        ]
        assert mission.execution_log[-1].metadata["reason"] == (
            "no_valid_option_found"
        )

    asyncio.run(scenario())


def test_monitoring_retry_is_capped_at_expiry_deadline() -> None:
    class EmptySearchAdapter(MockTrainAdapter):
        async def search_options(
            self,
            mission: Mission,
            identities: list[Identity],
        ) -> list[ProviderOption]:
            return []

    async def scenario() -> None:
        identity_repository = InMemoryIdentityRepository()
        mission_repository = InMemoryMissionRepository()
        current_time = aware_datetime()
        identity = await identity_repository.create(make_identity())
        mission = make_mission(
            [identity.id],
            scheduled_at=current_time,
        )
        mission.expires_at = current_time + timedelta(seconds=10)
        await mission_repository.create(mission)
        resolver = ProviderResolver(
            ProviderRegistry([EmptySearchAdapter()])
        )

        first = await process_due_missions(
            mission_repository,
            identity_repository,
            current_time,
            provider_resolver=resolver,
        )
        at_deadline = await process_due_missions(
            mission_repository,
            identity_repository,
            mission.expires_at,
            provider_resolver=resolver,
        )

        assert first.retry_scheduled_mission_ids == [mission.id]
        assert mission.scheduled_at == mission.expires_at
        assert at_deadline.expired_mission_ids == [mission.id]
        assert at_deadline.processed_count == 0
        assert mission.status is MissionStatus.expired
        assert mission.execution_attempts == 1

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


def test_expired_due_mission_is_expired_without_claiming() -> None:
    async def scenario() -> None:
        identity_repository = InMemoryIdentityRepository()
        mission_repository = InMemoryMissionRepository()
        current_time = aware_datetime()
        identity = await identity_repository.create(make_identity())
        mission = make_mission(
            [identity.id],
            scheduled_at=current_time - timedelta(minutes=2),
        )
        mission.expires_at = current_time - timedelta(minutes=1)
        await mission_repository.create(mission)

        result = await process_due_missions(
            mission_repository,
            identity_repository,
            current_time,
        )

        stored = await mission_repository.get(mission.id)
        assert result.processed_count == 0
        assert result.expired_mission_ids == [mission.id]
        assert stored is not None
        assert stored.status is MissionStatus.expired
        assert stored.execution_attempts == 0
        assert await mission_repository.list_execution_attempts(
            mission.id
        ) == []

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
    provider_id: str | None = None,
) -> Mission:
    return Mission(
        id=uuid4(),
        type=MissionType.train_trip,
        title="Moscow to Saint Petersburg",
        status=MissionStatus.waiting,
        participant_ids=participant_ids,
        provider="mock_train",
        provider_id=provider_id,
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
    return datetime(2026, 8, 1, 10, 0, tzinfo=UTC)


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

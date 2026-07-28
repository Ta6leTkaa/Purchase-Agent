import asyncio
from collections.abc import Iterator
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
from app.domain.provider import ProviderOption, ReservationResult
from app.services.mission_engine import (
    InvalidMissionConfirmationError,
    InvalidMissionRunError,
    MissionNotReadyError,
    UnsupportedMissionTypeError,
    confirm_mission,
    run_mission,
)
from app.services.mission_errors import MissionNotFoundError
from app.services.provider_errors import ProviderOperationError
from app.storage.memory import InMemoryIdentityRepository, InMemoryMissionRepository


@pytest.fixture
def repositories() -> Iterator[
    tuple[InMemoryIdentityRepository, InMemoryMissionRepository]
]:
    identity_repository = InMemoryIdentityRepository()
    mission_repository = InMemoryMissionRepository()
    yield identity_repository, mission_repository
    asyncio.run(identity_repository.clear())
    asyncio.run(mission_repository.clear())


def create_identity(
    identity_repository: InMemoryIdentityRepository,
) -> Identity:
    identity = Identity(
        id=uuid4(),
        display_name="Ivan Petrov",
        first_name="Ivan",
        last_name="Petrov",
        birth_date=date(1990, 1, 1),
    )
    return asyncio.run(identity_repository.create(identity))


def create_mission(
    mission_repository: InMemoryMissionRepository,
    participant_ids: list[UUID],
) -> Mission:
    mission = Mission(
        id=uuid4(),
        type=MissionType.train_trip,
        title="Moscow to Saint Petersburg",
        participant_ids=participant_ids,
        provider="mock_train",
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Saint Petersburg",
            travel_date=date(2026, 8, 1),
            passengers_count=4,
            must_be_same_compartment=True,
            min_lower_berths=2,
            max_total_price=30000,
            avoid_toilet=True,
        ),
        fallback_rules=FallbackRules(
            allow_adjacent_compartments=True,
        ),
    )
    return asyncio.run(mission_repository.create(mission))


def test_run_mission_sets_requires_confirmation_and_selects_best_option(
    repositories: tuple[InMemoryIdentityRepository, InMemoryMissionRepository],
) -> None:
    identity_repository, mission_repository = repositories
    identities = [create_identity(identity_repository) for _ in range(4)]
    mission = create_mission(
        mission_repository,
        [identity.id for identity in identities],
    )

    updated_mission = asyncio.run(
        run_mission(mission.id, mission_repository, identity_repository)
    )

    assert updated_mission.status is MissionStatus.requires_confirmation
    assert updated_mission.best_option is not None
    assert updated_mission.best_option.train_number == "001A"
    assert updated_mission.resolved_provider_id == "mock_train"
    assert updated_mission.reservation_id == f"mock-reservation-mission:{mission.id}"
    reservation_event = next(
        event
        for event in updated_mission.execution_log
        if event.type == "reservation_succeeded"
    )
    assert reservation_event.metadata == {
        "reservation_id": f"mock-reservation-mission:{mission.id}",
        "requires_confirmation": True,
    }
    resolution_event = updated_mission.execution_log[0]
    assert resolution_event.type == "provider_resolved"
    assert resolution_event.metadata == {
        "provider_id": "mock_train",
        "mission_type": "train_ticket",
        "selection_mode": "automatic",
        "snapshot": {
            "selection_mode": "automatic",
            "requested_provider_id": None,
            "resolved_provider_id": "mock_train",
            "candidate_provider_ids": ["mock_train"],
            "mission_type": "train_ticket",
        },
    }


def test_run_mission_resolves_adapter_once_without_setting_provider_id(
    repositories: tuple[InMemoryIdentityRepository, InMemoryMissionRepository],
) -> None:
    class CountingResolver:
        def __init__(self, adapter: MockTrainAdapter) -> None:
            self.adapter = adapter
            self.calls = 0

        def resolve(self, mission: Mission) -> MockTrainAdapter:
            self.calls += 1
            return self.adapter

    identity_repository, mission_repository = repositories
    identities = [create_identity(identity_repository) for _ in range(4)]
    mission = create_mission(
        mission_repository,
        [identity.id for identity in identities],
    )
    resolver = CountingResolver(MockTrainAdapter())

    updated_mission = asyncio.run(
        run_mission(
            mission.id,
            mission_repository,
            identity_repository,
            resolver,  # type: ignore[arg-type]
        )
    )

    assert resolver.calls == 1
    assert updated_mission.status is MissionStatus.requires_confirmation
    assert updated_mission.provider_id is None
    assert updated_mission.resolved_provider_id == "mock_train"


def test_run_mission_passes_stable_reservation_idempotency_key(
    repositories: tuple[InMemoryIdentityRepository, InMemoryMissionRepository],
) -> None:
    class CapturingAdapter(MockTrainAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.idempotency_keys: list[str] = []

        async def reserve_option(
            self,
            option: ProviderOption,
            mission: Mission,
            *,
            idempotency_key: str,
        ) -> ReservationResult:
            self.idempotency_keys.append(idempotency_key)
            return await super().reserve_option(
                option,
                mission,
                idempotency_key=idempotency_key,
            )

    class StaticResolver:
        def __init__(self, adapter: CapturingAdapter) -> None:
            self._adapter = adapter

        def resolve(self, mission: Mission) -> CapturingAdapter:
            return self._adapter

    identity_repository, mission_repository = repositories
    identities = [create_identity(identity_repository) for _ in range(4)]
    mission = create_mission(
        mission_repository,
        [identity.id for identity in identities],
    )
    adapter = CapturingAdapter()

    asyncio.run(
        run_mission(
            mission.id,
            mission_repository,
            identity_repository,
            StaticResolver(adapter),  # type: ignore[arg-type]
        )
    )

    assert adapter.idempotency_keys == [f"mission:{mission.id}"]


def test_run_mission_records_explicit_provider_resolution(
    repositories: tuple[InMemoryIdentityRepository, InMemoryMissionRepository],
) -> None:
    identity_repository, mission_repository = repositories
    identities = [create_identity(identity_repository) for _ in range(4)]
    mission = create_mission(
        mission_repository,
        [identity.id for identity in identities],
    )
    mission.provider_id = "mock_train"
    asyncio.run(mission_repository.update(mission))

    updated_mission = asyncio.run(
        run_mission(mission.id, mission_repository, identity_repository)
    )

    resolution_event = updated_mission.execution_log[0]
    assert resolution_event.type == "provider_resolved"
    assert resolution_event.metadata["selection_mode"] == "explicit"
    assert resolution_event.metadata["snapshot"] == {
        "selection_mode": "explicit",
        "requested_provider_id": "mock_train",
        "resolved_provider_id": "mock_train",
        "candidate_provider_ids": ["mock_train"],
        "mission_type": "train_ticket",
    }


def test_run_waiting_mission_is_allowed(
    repositories: tuple[InMemoryIdentityRepository, InMemoryMissionRepository],
) -> None:
    identity_repository, mission_repository = repositories
    identities = [create_identity(identity_repository) for _ in range(4)]
    mission = create_mission(
        mission_repository,
        [identity.id for identity in identities],
    )
    mission.status = MissionStatus.waiting
    asyncio.run(mission_repository.update(mission))

    updated_mission = asyncio.run(
        run_mission(mission.id, mission_repository, identity_repository)
    )

    assert updated_mission.status is MissionStatus.requires_confirmation


def test_run_waiting_mission_before_scheduled_time_is_rejected(
    repositories: tuple[InMemoryIdentityRepository, InMemoryMissionRepository],
) -> None:
    identity_repository, mission_repository = repositories
    identities = [create_identity(identity_repository) for _ in range(4)]
    current_time = datetime(2026, 7, 16, 10, 0, tzinfo=timezone.utc)
    mission = create_mission(
        mission_repository,
        [identity.id for identity in identities],
    )
    mission.status = MissionStatus.waiting
    mission.scheduled_at = current_time + timedelta(hours=1)
    asyncio.run(mission_repository.update(mission))

    with pytest.raises(MissionNotReadyError):
        asyncio.run(
            run_mission(
                mission.id,
                mission_repository,
                identity_repository,
                current_time=current_time,
            )
        )

    stored_mission = asyncio.run(mission_repository.get(mission.id))
    assert stored_mission is not None
    assert stored_mission.status is MissionStatus.waiting
    assert stored_mission.execution_log == []


def test_run_waiting_mission_after_scheduled_time_is_allowed(
    repositories: tuple[InMemoryIdentityRepository, InMemoryMissionRepository],
) -> None:
    identity_repository, mission_repository = repositories
    identities = [create_identity(identity_repository) for _ in range(4)]
    current_time = datetime(2026, 7, 16, 10, 0, tzinfo=timezone.utc)
    mission = create_mission(
        mission_repository,
        [identity.id for identity in identities],
    )
    mission.status = MissionStatus.waiting
    mission.scheduled_at = current_time
    asyncio.run(mission_repository.update(mission))

    updated_mission = asyncio.run(
        run_mission(
            mission.id,
            mission_repository,
            identity_repository,
            current_time=current_time,
        )
    )

    assert updated_mission.status is MissionStatus.requires_confirmation


def test_run_processing_mission_is_allowed_for_processor(
    repositories: tuple[InMemoryIdentityRepository, InMemoryMissionRepository],
) -> None:
    identity_repository, mission_repository = repositories
    identities = [create_identity(identity_repository) for _ in range(4)]
    mission = create_mission(
        mission_repository,
        [identity.id for identity in identities],
    )
    mission.status = MissionStatus.processing
    mission.claimed_at = datetime.now(timezone.utc)
    asyncio.run(mission_repository.update(mission))

    updated_mission = asyncio.run(
        run_mission(
            mission.id,
            mission_repository,
            identity_repository,
            allow_processing=True,
        )
    )

    assert updated_mission.status is MissionStatus.requires_confirmation
    assert updated_mission.claimed_at is None


def test_run_mission_adds_execution_events(
    repositories: tuple[InMemoryIdentityRepository, InMemoryMissionRepository],
) -> None:
    identity_repository, mission_repository = repositories
    identities = [create_identity(identity_repository) for _ in range(4)]
    mission = create_mission(
        mission_repository,
        [identity.id for identity in identities],
    )

    updated_mission = asyncio.run(
        run_mission(mission.id, mission_repository, identity_repository)
    )
    event_types = [event.type for event in updated_mission.execution_log]

    assert "mission_started" in event_types
    assert "search_started" in event_types
    assert "options_found" in event_types
    assert "best_option_selected" in event_types
    assert "reservation_started" in event_types
    assert "reservation_succeeded" in event_types
    assert "waiting_for_user_confirmation" in event_types


def test_run_mission_fails_when_participant_is_missing(
    repositories: tuple[InMemoryIdentityRepository, InMemoryMissionRepository],
) -> None:
    identity_repository, mission_repository = repositories
    mission = create_mission(mission_repository, [uuid4()])

    updated_mission = asyncio.run(
        run_mission(mission.id, mission_repository, identity_repository)
    )

    assert updated_mission.status is MissionStatus.failed
    assert updated_mission.execution_log[-1].type == "participant_missing"


def test_run_mission_persists_typed_provider_search_failure(
    repositories: tuple[InMemoryIdentityRepository, InMemoryMissionRepository],
) -> None:
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

    class StaticResolver:
        def __init__(self, adapter: FailingSearchAdapter) -> None:
            self._adapter = adapter

        def resolve(self, mission: Mission) -> FailingSearchAdapter:
            return self._adapter

    identity_repository, mission_repository = repositories
    identities = [create_identity(identity_repository) for _ in range(4)]
    mission = create_mission(
        mission_repository,
        [identity.id for identity in identities],
    )

    updated_mission = asyncio.run(
        run_mission(
            mission.id,
            mission_repository,
            identity_repository,
            StaticResolver(FailingSearchAdapter()),  # type: ignore[arg-type]
        )
    )

    assert updated_mission.status is MissionStatus.failed
    assert updated_mission.resolved_provider_id == "mock_train"
    assert updated_mission.execution_log[-1].type == "provider_operation_failed"
    assert updated_mission.execution_log[-1].metadata == {
        "provider_id": "mock_train",
        "operation": "search",
    }


def test_run_mission_does_not_persist_provider_failure_message(
    repositories: tuple[InMemoryIdentityRepository, InMemoryMissionRepository],
) -> None:
    class DeclinedReservationAdapter(MockTrainAdapter):
        async def reserve_option(
            self,
            option: ProviderOption,
            mission: Mission,
            *,
            idempotency_key: str,
        ) -> ReservationResult:
            del option, mission, idempotency_key
            return ReservationResult(
                success=False,
                message="provider token: must-not-be-persisted",
            )

    class StaticResolver:
        def __init__(self, adapter: DeclinedReservationAdapter) -> None:
            self._adapter = adapter

        def resolve(self, mission: Mission) -> DeclinedReservationAdapter:
            return self._adapter

    identity_repository, mission_repository = repositories
    identities = [create_identity(identity_repository) for _ in range(4)]
    mission = create_mission(
        mission_repository,
        [identity.id for identity in identities],
    )

    updated_mission = asyncio.run(
        run_mission(
            mission.id,
            mission_repository,
            identity_repository,
            StaticResolver(DeclinedReservationAdapter()),  # type: ignore[arg-type]
        )
    )

    assert updated_mission.status is MissionStatus.failed
    reservation_event = updated_mission.execution_log[-1]
    assert reservation_event.type == "reservation_failed"
    assert reservation_event.metadata == {"provider_id": "mock_train"}
    assert "must-not-be-persisted" not in reservation_event.message


def test_run_unknown_mission_raises_mission_not_found_error(
    repositories: tuple[InMemoryIdentityRepository, InMemoryMissionRepository],
) -> None:
    identity_repository, mission_repository = repositories

    with pytest.raises(MissionNotFoundError):
        asyncio.run(
            run_mission(uuid4(), mission_repository, identity_repository)
        )


def test_run_mission_fails_fast_for_unsupported_provider_capability(
    repositories: tuple[InMemoryIdentityRepository, InMemoryMissionRepository],
) -> None:
    class UnsupportedResolver:
        def __init__(self) -> None:
            self.calls = 0

        def resolve(self, mission: Mission) -> object:
            self.calls += 1
            raise UnsupportedMissionTypeError(
                provider_id="unsupported-provider",
                mission_type=mission.mission_type,
            )

    identity_repository, mission_repository = repositories
    mission = create_mission(mission_repository, [uuid4()])
    mission.provider_id = "unsupported-provider"
    asyncio.run(mission_repository.update(mission))
    resolver = UnsupportedResolver()

    with pytest.raises(UnsupportedMissionTypeError) as exc_info:
        asyncio.run(
            run_mission(
                mission.id,
                mission_repository,
                identity_repository,
                resolver,  # type: ignore[arg-type]
            )
        )

    stored_mission = asyncio.run(mission_repository.get(mission.id))
    assert "unsupported-provider" in str(exc_info.value)
    assert mission.mission_type.value in str(exc_info.value)
    assert resolver.calls == 1
    assert stored_mission is not None
    assert stored_mission.status is MissionStatus.created
    assert stored_mission.resolved_provider_id is None
    assert stored_mission.execution_log[-1].type == "provider_resolution_failed"
    assert stored_mission.execution_log[-1].metadata == {
        "reason": "unsupported_mission_type",
        "mission_type": "train_ticket",
        "requested_provider_id": "unsupported-provider",
        "candidate_provider_ids": [],
    }
    assert "snapshot" not in stored_mission.execution_log[-1].metadata


@pytest.mark.parametrize(
    "status",
    [
        MissionStatus.running,
        MissionStatus.processing,
        MissionStatus.searching,
        MissionStatus.requires_confirmation,
        MissionStatus.completed,
        MissionStatus.failed,
    ],
)
def test_run_mission_rejects_invalid_start_statuses(
    repositories: tuple[InMemoryIdentityRepository, InMemoryMissionRepository],
    status: MissionStatus,
) -> None:
    identity_repository, mission_repository = repositories
    mission = create_mission(mission_repository, [uuid4()])
    mission.status = status
    mission.execution_log = []
    asyncio.run(mission_repository.update(mission))

    with pytest.raises(InvalidMissionRunError) as exc_info:
        asyncio.run(
            run_mission(mission.id, mission_repository, identity_repository)
        )

    stored_mission = asyncio.run(mission_repository.get(mission.id))
    assert status.value in str(exc_info.value)
    assert stored_mission is not None
    assert stored_mission.status is status
    assert stored_mission.execution_log == []


def test_run_mission_with_isolated_repositories() -> None:
    identity_repository = InMemoryIdentityRepository()
    mission_repository = InMemoryMissionRepository()
    identities = [create_identity(identity_repository) for _ in range(4)]
    mission = create_mission(
        mission_repository,
        [identity.id for identity in identities],
    )

    updated_mission = asyncio.run(
        run_mission(mission.id, mission_repository, identity_repository)
    )

    assert updated_mission.status is MissionStatus.requires_confirmation
    assert updated_mission.best_option is not None
    assert updated_mission.best_option.train_number == "001A"


def test_confirm_mission_sets_completed_and_adds_events(
    repositories: tuple[InMemoryIdentityRepository, InMemoryMissionRepository],
) -> None:
    _identity_repository, mission_repository = repositories
    mission = create_mission(mission_repository, [uuid4()])
    mission.status = MissionStatus.requires_confirmation
    asyncio.run(mission_repository.update(mission))

    updated_mission = asyncio.run(
        confirm_mission(mission.id, mission_repository)
    )
    event_types = [event.type for event in updated_mission.execution_log]

    assert updated_mission.status is MissionStatus.completed
    assert "mission_confirmed" in event_types
    assert "mission_completed" in event_types


def test_confirm_created_mission_raises_invalid_confirmation_error(
    repositories: tuple[InMemoryIdentityRepository, InMemoryMissionRepository],
) -> None:
    _identity_repository, mission_repository = repositories
    mission = create_mission(mission_repository, [uuid4()])

    with pytest.raises(InvalidMissionConfirmationError) as exc_info:
        asyncio.run(confirm_mission(mission.id, mission_repository))

    assert "created" in str(exc_info.value)


def test_confirm_unknown_mission_raises_mission_not_found_error(
    repositories: tuple[InMemoryIdentityRepository, InMemoryMissionRepository],
) -> None:
    _identity_repository, mission_repository = repositories

    with pytest.raises(MissionNotFoundError):
        asyncio.run(confirm_mission(uuid4(), mission_repository))


def test_confirm_completed_mission_twice_is_rejected(
    repositories: tuple[InMemoryIdentityRepository, InMemoryMissionRepository],
) -> None:
    _identity_repository, mission_repository = repositories
    mission = create_mission(mission_repository, [uuid4()])
    mission.status = MissionStatus.requires_confirmation
    asyncio.run(mission_repository.update(mission))
    asyncio.run(confirm_mission(mission.id, mission_repository))

    with pytest.raises(InvalidMissionConfirmationError) as exc_info:
        asyncio.run(confirm_mission(mission.id, mission_repository))

    assert "completed" in str(exc_info.value)

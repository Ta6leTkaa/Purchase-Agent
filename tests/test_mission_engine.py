import asyncio
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.domain.identity import Identity
from app.domain.mission import (
    FallbackRules,
    Mission,
    MissionStatus,
    MissionType,
    TrainConstraints,
)
from app.services.mission_engine import (
    InvalidMissionConfirmationError,
    InvalidMissionRunError,
    MissionNotReadyError,
    MissionNotFoundError,
    confirm_mission,
    run_mission,
)
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


def test_run_unknown_mission_raises_mission_not_found_error(
    repositories: tuple[InMemoryIdentityRepository, InMemoryMissionRepository],
) -> None:
    identity_repository, mission_repository = repositories

    with pytest.raises(MissionNotFoundError):
        asyncio.run(
            run_mission(uuid4(), mission_repository, identity_repository)
        )


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

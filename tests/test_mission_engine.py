import asyncio
from collections.abc import Iterator
from datetime import date
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
from app.services.mission_engine import MissionNotFoundError, run_mission
from app.storage.memory import store


@pytest.fixture(autouse=True)
def clear_store() -> Iterator[None]:
    store.clear()
    yield
    store.clear()


def create_identity() -> Identity:
    identity = Identity(
        id=uuid4(),
        display_name="Ivan Petrov",
        first_name="Ivan",
        last_name="Petrov",
        birth_date=date(1990, 1, 1),
    )
    return store.create_identity(identity)


def create_mission(participant_ids: list[UUID]) -> Mission:
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
    return store.create_mission(mission)


def test_run_mission_sets_requires_confirmation_and_selects_best_option() -> None:
    identities = [create_identity() for _ in range(4)]
    mission = create_mission([identity.id for identity in identities])

    updated_mission = asyncio.run(run_mission(mission.id))

    assert updated_mission.status is MissionStatus.requires_confirmation
    assert updated_mission.best_option is not None
    assert updated_mission.best_option.train_number == "001A"


def test_run_mission_adds_execution_events() -> None:
    identities = [create_identity() for _ in range(4)]
    mission = create_mission([identity.id for identity in identities])

    updated_mission = asyncio.run(run_mission(mission.id))
    event_types = [event.type for event in updated_mission.execution_log]

    assert "mission_started" in event_types
    assert "search_started" in event_types
    assert "options_found" in event_types
    assert "best_option_selected" in event_types
    assert "reservation_started" in event_types
    assert "waiting_for_user_confirmation" in event_types


def test_run_mission_fails_when_participant_is_missing() -> None:
    mission = create_mission([uuid4()])

    updated_mission = asyncio.run(run_mission(mission.id))

    assert updated_mission.status is MissionStatus.failed
    assert updated_mission.execution_log[-1].type == "participant_missing"


def test_run_unknown_mission_raises_mission_not_found_error() -> None:
    with pytest.raises(MissionNotFoundError):
        asyncio.run(run_mission(uuid4()))

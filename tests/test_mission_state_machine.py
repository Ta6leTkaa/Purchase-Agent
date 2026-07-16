from datetime import date
from uuid import uuid4

import pytest

from app.domain.mission import (
    Mission,
    MissionStatus,
    MissionType,
    TrainConstraints,
)
from app.services.mission_state_machine import (
    InvalidMissionTransitionError,
    MissionStateMachine,
)


def test_allowed_status_transitions() -> None:
    state_machine = MissionStateMachine()
    mission = make_mission()

    state_machine.transition(mission, MissionStatus.running)
    assert mission.status is MissionStatus.running

    state_machine.transition(mission, MissionStatus.searching)
    assert mission.status is MissionStatus.searching

    state_machine.transition(mission, MissionStatus.option_found)
    assert mission.status is MissionStatus.option_found

    state_machine.transition(mission, MissionStatus.reserving)
    assert mission.status is MissionStatus.reserving

    state_machine.transition(mission, MissionStatus.requires_confirmation)
    assert mission.status is MissionStatus.requires_confirmation

    state_machine.transition(mission, MissionStatus.completed)
    assert mission.status is MissionStatus.completed


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (MissionStatus.created, MissionStatus.completed),
        (MissionStatus.searching, MissionStatus.created),
        (MissionStatus.completed, MissionStatus.running),
        (MissionStatus.failed, MissionStatus.running),
    ],
)
def test_invalid_status_transitions_are_rejected(
    current: MissionStatus,
    target: MissionStatus,
) -> None:
    state_machine = MissionStateMachine()
    mission = make_mission(status=current)

    with pytest.raises(InvalidMissionTransitionError) as exc_info:
        state_machine.transition(mission, target)

    assert current.value in str(exc_info.value)
    assert target.value in str(exc_info.value)
    assert mission.status is current


@pytest.mark.parametrize(
    ("current", "target", "expected"),
    [
        (MissionStatus.created, MissionStatus.running, True),
        (MissionStatus.created, MissionStatus.waiting, True),
        (MissionStatus.waiting, MissionStatus.running, True),
        (MissionStatus.running, MissionStatus.searching, True),
        (MissionStatus.running, MissionStatus.failed, True),
        (MissionStatus.searching, MissionStatus.option_found, True),
        (MissionStatus.searching, MissionStatus.failed, True),
        (MissionStatus.option_found, MissionStatus.reserving, True),
        (MissionStatus.option_found, MissionStatus.failed, True),
        (MissionStatus.reserving, MissionStatus.requires_confirmation, True),
        (MissionStatus.reserving, MissionStatus.completed, True),
        (MissionStatus.reserving, MissionStatus.failed, True),
        (MissionStatus.requires_confirmation, MissionStatus.completed, True),
        (MissionStatus.requires_confirmation, MissionStatus.failed, True),
        (MissionStatus.created, MissionStatus.completed, False),
        (MissionStatus.searching, MissionStatus.created, False),
        (MissionStatus.completed, MissionStatus.running, False),
        (MissionStatus.failed, MissionStatus.running, False),
    ],
)
def test_can_transition_returns_expected_bool(
    current: MissionStatus,
    target: MissionStatus,
    expected: bool,
) -> None:
    state_machine = MissionStateMachine()

    assert state_machine.can_transition(current, target) is expected


def make_mission(
    status: MissionStatus = MissionStatus.created,
) -> Mission:
    return Mission(
        id=uuid4(),
        type=MissionType.train_trip,
        title="Family train trip",
        status=status,
        participant_ids=[uuid4()],
        provider="mock_train",
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Saint Petersburg",
            travel_date=date(2026, 8, 1),
            passengers_count=1,
        ),
    )

from datetime import date, datetime, timezone
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


def test_processing_status_can_transition_to_terminal_results() -> None:
    state_machine = MissionStateMachine()

    for target in [
        MissionStatus.requires_confirmation,
        MissionStatus.completed,
        MissionStatus.failed,
    ]:
        mission = make_mission(status=MissionStatus.processing)

        state_machine.transition(mission, target)

        assert mission.status is target
        assert mission.claimed_at is None


def test_waiting_to_processing_sets_claimed_at() -> None:
    state_machine = MissionStateMachine()
    current_time = aware_datetime()
    mission = make_mission(status=MissionStatus.waiting)

    state_machine.transition(
        mission,
        MissionStatus.processing,
        current_time=current_time,
    )

    assert mission.status is MissionStatus.processing
    assert mission.claimed_at == current_time


@pytest.mark.parametrize(
    "target",
    [
        MissionStatus.requires_confirmation,
        MissionStatus.completed,
        MissionStatus.failed,
    ],
)
def test_processing_exit_clears_claimed_at(target: MissionStatus) -> None:
    state_machine = MissionStateMachine()
    mission = make_mission(status=MissionStatus.processing)

    state_machine.transition(mission, target)

    assert mission.status is target
    assert mission.claimed_at is None


def test_processing_transition_rejects_naive_current_time() -> None:
    state_machine = MissionStateMachine()
    mission = make_mission(status=MissionStatus.waiting)

    with pytest.raises(InvalidMissionTransitionError):
        state_machine.transition(
            mission,
            MissionStatus.processing,
            current_time=datetime(2026, 8, 1, 10, 0),
        )

    assert mission.status is MissionStatus.waiting
    assert mission.claimed_at is None


def test_processing_transition_requires_current_time() -> None:
    state_machine = MissionStateMachine()
    mission = make_mission(status=MissionStatus.waiting)

    with pytest.raises(InvalidMissionTransitionError):
        state_machine.transition(mission, MissionStatus.processing)

    assert mission.status is MissionStatus.waiting
    assert mission.claimed_at is None


def test_processing_mission_requires_claimed_at() -> None:
    with pytest.raises(ValueError):
        Mission(
            id=uuid4(),
            type=MissionType.train_trip,
            title="Family train trip",
            status=MissionStatus.processing,
            participant_ids=[uuid4()],
            provider="mock_train",
            constraints=TrainConstraints(
                from_city="Moscow",
                to_city="Saint Petersburg",
                travel_date=date(2026, 8, 1),
                passengers_count=1,
            ),
        )


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
        (MissionStatus.waiting, MissionStatus.processing, True),
        (MissionStatus.waiting, MissionStatus.running, True),
        (MissionStatus.processing, MissionStatus.requires_confirmation, True),
        (MissionStatus.processing, MissionStatus.completed, True),
        (MissionStatus.processing, MissionStatus.failed, True),
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
        claimed_at=(
            aware_datetime()
            if status is MissionStatus.processing
            else None
        ),
    )


def aware_datetime() -> datetime:
    return datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)

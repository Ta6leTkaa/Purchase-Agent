from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from app.domain.mission import Mission, MissionStatus, TrainConstraints
from app.services.mission_outcome import MissionNextAction, get_mission_outcome


def make_mission(
    status: MissionStatus,
    *,
    execution_attempts: int = 0,
    max_execution_attempts: int = 3,
) -> Mission:
    return Mission(
        id=uuid4(),
        title="Moscow to Saint Petersburg",
        status=status,
        participant_ids=[uuid4()],
        provider="mock_train",
        claimed_at=(
            datetime(2026, 8, 1, tzinfo=UTC)
            if status is MissionStatus.processing
            else None
        ),
        execution_attempts=execution_attempts,
        max_execution_attempts=max_execution_attempts,
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Saint Petersburg",
            travel_date=date(2026, 9, 1),
            passengers_count=1,
        ),
    )


@pytest.mark.parametrize(
    ("status", "next_action"),
    [
        (MissionStatus.created, MissionNextAction.RUN),
        (MissionStatus.waiting, MissionNextAction.WAIT),
        (MissionStatus.processing, MissionNextAction.WAIT),
        (MissionStatus.paused, MissionNextAction.RESUME),
        (MissionStatus.requires_confirmation, MissionNextAction.CONFIRM),
        (MissionStatus.failed, MissionNextAction.RETRY),
        (MissionStatus.completed, MissionNextAction.NONE),
        (MissionStatus.cancelled, MissionNextAction.NONE),
        (MissionStatus.expired, MissionNextAction.NONE),
    ],
)
def test_maps_status_to_action(
    status: MissionStatus,
    next_action: MissionNextAction,
) -> None:
    outcome = get_mission_outcome(make_mission(status))

    assert outcome.status is status
    assert outcome.next_action is next_action
    assert outcome.terminal is (status in {
        MissionStatus.completed,
        MissionStatus.cancelled,
        MissionStatus.expired,
    })
    assert outcome.successful is (status is MissionStatus.completed)


def test_exhausted_failure_has_no_retry_action() -> None:
    outcome = get_mission_outcome(
        make_mission(
            MissionStatus.failed,
            execution_attempts=3,
            max_execution_attempts=3,
        )
    )

    assert outcome.next_action is MissionNextAction.NONE
    assert outcome.terminal

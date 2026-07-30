from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.mission import Mission, MissionStatus, MissionType, TrainConstraints
from app.services.mission_retry_policy import MissionRetryPolicy

CURRENT_TIME = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)


def make_failed_mission(
    *,
    attempts: int = 1,
    max_attempts: int = 3,
    retryable: bool = True,
) -> Mission:
    mission = Mission(
        id=uuid4(),
        type=MissionType.TRAIN_TICKET,
        title="Retry policy mission",
        status=MissionStatus.failed,
        participant_ids=[uuid4()],
        provider="mock_train",
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Saint Petersburg",
            travel_date=date(2026, 8, 2),
            passengers_count=1,
        ),
        execution_attempts=attempts,
        max_execution_attempts=max_attempts,
    )
    mission.record_event(
        timestamp=CURRENT_TIME,
        event_type="provider_operation_failed",
        message="Provider operation failed.",
        metadata={"retryable": retryable},
    )
    return mission


def test_retry_policy_uses_bounded_exponential_backoff() -> None:
    policy = MissionRetryPolicy(
        initial_delay=timedelta(seconds=10),
        maximum_delay=timedelta(seconds=50),
        multiplier=3,
    )

    assert policy.delay_after_attempt(1) == timedelta(seconds=10)
    assert policy.delay_after_attempt(2) == timedelta(seconds=30)
    assert policy.delay_after_attempt(3) == timedelta(seconds=50)
    assert policy.delay_after_attempt(8) == timedelta(seconds=50)


def test_retry_policy_requires_retryable_failure_and_available_attempt() -> None:
    policy = MissionRetryPolicy()

    assert policy.should_retry_mission(make_failed_mission()) is True
    assert (
        policy.should_retry_mission(make_failed_mission(retryable=False))
        is False
    )
    assert (
        policy.should_retry_mission(
            make_failed_mission(attempts=3, max_attempts=3)
        )
        is False
    )
    assert policy.should_retry_exception(TimeoutError()) is True
    assert policy.should_retry_exception(ConnectionError()) is True
    assert policy.should_retry_exception(RuntimeError()) is False


def test_retry_policy_retries_when_no_valid_option_is_available() -> None:
    mission = make_failed_mission()
    mission.execution_log = []
    mission.last_event_sequence = 0
    mission.record_event(
        timestamp=CURRENT_TIME,
        event_type="no_valid_option_found",
        message="No valid option found.",
    )

    assert MissionRetryPolicy().should_retry_mission(mission) is True


@pytest.mark.parametrize(
    "kwargs",
    [
        {"initial_delay": timedelta(0)},
        {
            "initial_delay": timedelta(seconds=2),
            "maximum_delay": timedelta(seconds=1),
        },
        {"multiplier": 0},
    ],
)
def test_retry_policy_rejects_invalid_configuration(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        MissionRetryPolicy(**kwargs)  # type: ignore[arg-type]


def test_retry_policy_rejects_invalid_attempt_number() -> None:
    with pytest.raises(ValueError):
        MissionRetryPolicy().delay_after_attempt(0)

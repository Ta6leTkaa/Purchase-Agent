from dataclasses import dataclass
from datetime import timedelta

from app.domain.mission import Mission


@dataclass(frozen=True, slots=True)
class MissionRetryPolicy:
    initial_delay: timedelta = timedelta(seconds=30)
    maximum_delay: timedelta = timedelta(minutes=15)
    multiplier: int = 2

    def __post_init__(self) -> None:
        if self.initial_delay <= timedelta(0):
            raise ValueError("initial_delay must be greater than zero")
        if self.maximum_delay < self.initial_delay:
            raise ValueError(
                "maximum_delay must be greater than or equal to initial_delay"
            )
        if self.multiplier < 1:
            raise ValueError("multiplier must be at least one")

    def delay_after_attempt(self, attempt_number: int) -> timedelta:
        if attempt_number < 1:
            raise ValueError("attempt_number must be at least one")
        delay = self.initial_delay
        for _ in range(attempt_number - 1):
            delay = min(delay * self.multiplier, self.maximum_delay)
        return delay

    def should_retry_mission(self, mission: Mission) -> bool:
        if mission.has_exhausted_attempts or not mission.execution_log:
            return False
        event = mission.execution_log[-1]
        if event.type == "no_valid_option_found":
            return True
        return (
            event.type == "provider_operation_failed"
            and event.metadata.get("retryable") is True
        )

    def should_retry_exception(self, error: Exception) -> bool:
        return isinstance(error, (ConnectionError, TimeoutError))


default_mission_retry_policy = MissionRetryPolicy()

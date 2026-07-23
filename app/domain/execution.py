from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class InvalidMissionEventSequenceError(ValueError):
    pass


class ExecutionEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence: int = Field(ge=1)
    timestamp: datetime
    type: str
    message: str
    metadata: dict[str, Any] = {}


def validate_event_sequence(
    events: list[ExecutionEvent],
    *,
    last_event_sequence: int,
) -> None:
    previous_sequence = 0
    for position, event in enumerate(events, start=1):
        if event.sequence <= previous_sequence:
            raise InvalidMissionEventSequenceError(
                "Mission event sequences must be strictly increasing "
                f"at position {position}"
            )
        previous_sequence = event.sequence
    if last_event_sequence != previous_sequence:
        raise InvalidMissionEventSequenceError(
            "last_event_sequence must match the final event sequence"
        )

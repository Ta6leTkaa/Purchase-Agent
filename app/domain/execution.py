from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class InvalidMissionEventSequenceError(ValueError):
    pass


class InvalidMissionEventIdError(ValueError):
    pass


EventIdFactory = Callable[[], UUID]


def new_mission_event_id() -> UUID:
    return uuid4()


class ExecutionEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: UUID = Field(default_factory=new_mission_event_id)
    correlation_id: UUID = Field(default_factory=new_mission_event_id)
    causation_id: UUID | None = None
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
    event_ids: set[UUID] = set()
    for position, event in enumerate(events, start=1):
        if event.sequence <= previous_sequence:
            raise InvalidMissionEventSequenceError(
                "Mission event sequences must be strictly increasing "
                f"at position {position}"
            )
        previous_sequence = event.sequence
        if event.event_id.int == 0:
            raise InvalidMissionEventIdError("Mission event ID must not be nil")
        if event.event_id in event_ids:
            raise InvalidMissionEventIdError(
                f"Mission event IDs must be unique at position {position}"
            )
        event_ids.add(event.event_id)
        if event.correlation_id.int == 0:
            raise InvalidMissionEventIdError("Mission correlation ID must not be nil")
        if event.causation_id is not None:
            if event.causation_id.int == 0 or event.causation_id == event.event_id:
                raise InvalidMissionEventIdError("Mission event causation ID is invalid")
    if last_event_sequence != previous_sequence:
        raise InvalidMissionEventSequenceError(
            "last_event_sequence must match the final event sequence"
        )

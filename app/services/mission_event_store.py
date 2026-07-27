from collections.abc import Sequence
from typing import Any, Protocol
from uuid import UUID

from app.domain.execution import ExecutionEvent, validate_event_sequence
from app.services.mission_event_serializer import (
    MissionEventSerializer,
    mission_event_serializer,
)
from app.services.mission_event_upcaster import MissionEventDeserializationContext


class MissionEventStore(Protocol):
    async def load(self, mission_id: UUID) -> Sequence[ExecutionEvent]:
        ...

    async def append(
        self,
        mission_id: UUID,
        events: Sequence[ExecutionEvent],
    ) -> None:
        ...


class MissionJsonEventStore:
    """Canonical Mission event adapter backed by the existing JSON column."""

    def __init__(
        self,
        serializer: MissionEventSerializer = mission_event_serializer,
    ) -> None:
        self._serializer = serializer

    def deserialize(
        self,
        events: Sequence[dict[str, object]],
        *,
        last_event_sequence: int,
        mission_id: UUID | None = None,
    ) -> list[ExecutionEvent]:
        restored: list[ExecutionEvent] = []
        for index, event in enumerate(events):
            previous_event = restored[-1] if restored else None
            restored.append(self._serializer.deserialize(
                event,
                context=MissionEventDeserializationContext(
                    mission_id=mission_id,
                    event_index=index,
                    previous_event_id=(
                        previous_event.event_id if previous_event else None
                    ),
                    previous_correlation_id=(
                        previous_event.correlation_id if previous_event else None
                    ),
                ),
            ))
        validate_event_sequence(
            restored,
            last_event_sequence=last_event_sequence,
        )
        return restored

    def serialize(self, events: Sequence[ExecutionEvent]) -> list[dict[str, Any]]:
        return [self._serializer.serialize(event) for event in events]

    def events_after(
        self,
        events: Sequence[ExecutionEvent],
        sequence: int,
    ) -> tuple[ExecutionEvent, ...]:
        return tuple(event for event in events if event.sequence > sequence)

    def indexed_events_after(
        self,
        events: Sequence[ExecutionEvent],
        sequence: int,
    ) -> tuple[tuple[int, ExecutionEvent], ...]:
        return tuple(
            (index, event)
            for index, event in enumerate(events)
            if event.sequence > sequence
        )


mission_json_event_store = MissionJsonEventStore(mission_event_serializer)

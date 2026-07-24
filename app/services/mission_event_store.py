from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from app.domain.execution import ExecutionEvent, validate_event_sequence


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

    def deserialize(
        self,
        events: Sequence[dict[str, object]],
        *,
        last_event_sequence: int,
    ) -> list[ExecutionEvent]:
        restored = [ExecutionEvent.model_validate(event) for event in events]
        validate_event_sequence(
            restored,
            last_event_sequence=last_event_sequence,
        )
        return restored

    def serialize(self, events: Sequence[ExecutionEvent]) -> list[dict[str, object]]:
        return [event.model_dump(mode="json") for event in events]

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


mission_json_event_store = MissionJsonEventStore()

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Protocol

from pydantic import ValidationError

from app.domain.execution import ExecutionEvent
from app.domain.provider_resolution import (
    ProviderResolutionFailedEventPayload,
    ProviderResolvedEventPayload,
    ProviderSelectionChangedEventPayload,
)


class MissionEventSerializationError(ValueError):
    """Raised when a persisted Mission event cannot be safely converted."""

    def __init__(
        self,
        *,
        operation: str,
        event_type: object | None,
        sequence: object | None,
    ) -> None:
        self.operation = operation
        self.event_type = event_type
        self.sequence = sequence
        super().__init__(
            "Unable to "
            f"{operation} Mission event type {event_type!r} at sequence "
            f"{sequence!r}"
        )


class MissionEventSerializer(Protocol):
    def serialize(self, event: ExecutionEvent) -> dict[str, Any]:
        ...

    def deserialize(self, raw: Mapping[str, Any]) -> ExecutionEvent:
        ...


class PydanticMissionEventSerializer:
    """Owns the persisted JSON representation of canonical Mission events."""

    _payload_types = MappingProxyType(
        {
            "provider_resolved": ProviderResolvedEventPayload,
            "provider_resolution_failed": ProviderResolutionFailedEventPayload,
            "provider_selection_changed": ProviderSelectionChangedEventPayload,
        }
    )
    _generic_event_types = frozenset(
        {
            "best_option_selected",
            "claim_recovered",
            "mission.completed",
            "mission.created",
            "mission_completed",
            "mission_confirmed",
            "mission_created",
            "mission_processing_failed",
            "mission_scheduled",
            "mission_started",
            "no_valid_option_found",
            "options_found",
            "participant_missing",
            "reservation_failed",
            "reservation_started",
            "search_started",
            "waiting_for_user_confirmation",
        }
    )

    def serialize(self, event: ExecutionEvent) -> dict[str, Any]:
        try:
            return self._with_serialized_payload(event).model_dump(mode="json")
        except (TypeError, ValidationError, ValueError) as exc:
            raise MissionEventSerializationError(
                operation="serialize",
                event_type=event.type,
                sequence=event.sequence,
            ) from exc

    def deserialize(self, raw: Mapping[str, Any]) -> ExecutionEvent:
        event_type = raw.get("type")
        sequence = raw.get("sequence")
        try:
            event = ExecutionEvent.model_validate(raw)
            return self._with_serialized_payload(event)
        except (TypeError, ValidationError, ValueError) as exc:
            raise MissionEventSerializationError(
                operation="deserialize",
                event_type=event_type,
                sequence=sequence,
            ) from exc

    def _with_serialized_payload(self, event: ExecutionEvent) -> ExecutionEvent:
        payload_type = self._payload_types.get(event.type)
        if payload_type is None:
            if event.type not in self._generic_event_types:
                raise ValueError("Unknown Mission event type")
            return event
        payload = payload_type.model_validate(event.metadata)
        return event.model_copy(
            update={"metadata": payload.model_dump(mode="json")}
        )


mission_event_serializer = PydanticMissionEventSerializer()

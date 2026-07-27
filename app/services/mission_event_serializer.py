from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Protocol
from uuid import UUID

from pydantic import ValidationError

from app.domain.execution import ExecutionEvent
from app.domain.provider_resolution import (
    ProviderResolutionFailedEventPayload,
    ProviderResolvedEventPayload,
    ProviderSelectionChangedEventPayload,
)
from app.services.mission_event_upcaster import (
    CURRENT_MISSION_EVENT_SCHEMA_VERSION,
    DEFAULT_MISSION_EVENT_UPCASTERS,
    LEGACY_MISSION_EVENT_SCHEMA_VERSION,
    InvalidMissionEventUpcastResultError,
    MissingMissionEventUpcasterError,
    MissionEventSchemaVersionError,
    MissionEventUpcaster,
    MissionEventDeserializationContext,
    UnsupportedMissionEventSchemaVersionError,
    build_mission_event_upcaster_registry,
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


class MissionEventDeserializationError(MissionEventSerializationError):
    pass


class MissionEventSerializer(Protocol):
    def serialize(self, event: ExecutionEvent) -> dict[str, Any]:
        ...

    def deserialize(
        self,
        raw: Mapping[str, Any],
        *,
        context: MissionEventDeserializationContext | None = None,
    ) -> ExecutionEvent:
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

    def __init__(
        self,
        *,
        current_schema_version: int = CURRENT_MISSION_EVENT_SCHEMA_VERSION,
        upcasters: tuple[MissionEventUpcaster, ...] = DEFAULT_MISSION_EVENT_UPCASTERS,
    ) -> None:
        self._current_schema_version = current_schema_version
        self._upcasters_by_source_version = (
            build_mission_event_upcaster_registry(
                upcasters,
                current_schema_version=current_schema_version,
            )
        )

    def serialize(self, event: ExecutionEvent) -> dict[str, Any]:
        try:
            persisted_event = self._with_serialized_payload(event).model_dump(
                mode="json"
            )
            self._validate_event_id(event.event_id)
            persisted_event["schema_version"] = self._current_schema_version
            return persisted_event
        except (TypeError, ValidationError, ValueError) as exc:
            raise MissionEventSerializationError(
                operation="serialize",
                event_type=event.type,
                sequence=event.sequence,
            ) from exc

    def deserialize(
        self,
        raw: Mapping[str, Any],
        *,
        context: MissionEventDeserializationContext | None = None,
    ) -> ExecutionEvent:
        event_type = raw.get("type")
        sequence = raw.get("sequence")
        try:
            current_raw = self._upcast_to_current(
                raw,
                context=context or MissionEventDeserializationContext(),
            )
            event = ExecutionEvent.model_validate(current_raw)
            self._validate_event_id(event.event_id)
            return self._with_serialized_payload(event)
        except MissionEventSchemaVersionError:
            raise
        except (TypeError, ValidationError, ValueError) as exc:
            raise MissionEventDeserializationError(
                operation="deserialize",
                event_type=event_type,
                sequence=sequence,
            ) from exc

    def _upcast_to_current(
        self,
        raw: Mapping[str, Any],
        *,
        context: MissionEventDeserializationContext,
    ) -> Mapping[str, Any]:
        event_type = raw.get("type")
        sequence = raw.get("sequence")
        schema_version = self._extract_schema_version(raw)
        if schema_version > self._current_schema_version:
            raise UnsupportedMissionEventSchemaVersionError(
                actual_version=schema_version,
                current_version=self._current_schema_version,
                event_type=event_type,
                sequence=sequence,
            )

        current_raw: Mapping[str, Any] = raw
        while schema_version < self._current_schema_version:
            upcaster = self._upcasters_by_source_version.get(schema_version)
            if upcaster is None:
                raise MissingMissionEventUpcasterError(
                    schema_version=schema_version,
                    event_type=event_type,
                    sequence=sequence,
                )
            upcasted_raw = upcaster.upcast(current_raw, context=context)
            self._validate_upcast_result(
                upcasted_raw,
                target_version=upcaster.target_version,
                event_type=event_type,
                sequence=sequence,
            )
            current_raw = upcasted_raw
            schema_version = upcaster.target_version

        if current_raw.get("schema_version") != self._current_schema_version:
            raise InvalidMissionEventUpcastResultError(
                schema_version=current_raw.get("schema_version"),
                event_type=event_type,
                sequence=sequence,
            )
        return current_raw

    def _extract_schema_version(self, raw: Mapping[str, Any]) -> int:
        if "schema_version" not in raw:
            return LEGACY_MISSION_EVENT_SCHEMA_VERSION
        schema_version = raw["schema_version"]
        if type(schema_version) is not int or schema_version < 0:
            raise MissionEventSchemaVersionError(
                schema_version=schema_version,
                event_type=raw.get("type"),
                sequence=raw.get("sequence"),
            )
        return schema_version

    def _validate_upcast_result(
        self,
        raw: object,
        *,
        target_version: int,
        event_type: object | None,
        sequence: object | None,
    ) -> None:
        if (
            not isinstance(raw, Mapping)
            or raw.get("schema_version") != target_version
        ):
            schema_version = (
                raw.get("schema_version")
                if isinstance(raw, Mapping)
                else None
            )
            raise InvalidMissionEventUpcastResultError(
                schema_version=schema_version,
                event_type=event_type,
                sequence=sequence,
            )

    def _validate_event_id(self, event_id: UUID) -> None:
        if event_id.int == 0:
            raise ValueError("Mission event ID must not be nil")

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

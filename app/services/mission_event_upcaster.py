from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol
from uuid import UUID, uuid5

LEGACY_MISSION_EVENT_SCHEMA_VERSION = 0
CURRENT_MISSION_EVENT_SCHEMA_VERSION = 3
MISSION_EVENT_ID_NAMESPACE = UUID("a42492d0-825a-4c3d-908c-678e4900753b")


@dataclass(frozen=True)
class MissionEventDeserializationContext:
    mission_id: UUID | None = None
    event_index: int | None = None
    previous_event_id: UUID | None = None
    previous_correlation_id: UUID | None = None


class MissingMissionEventDeserializationContextError(ValueError):
    pass


def derive_legacy_event_id(*, mission_id: UUID, sequence: int) -> UUID:
    return uuid5(
        MISSION_EVENT_ID_NAMESPACE,
        f"mission-event:v1:{mission_id}:{sequence}",
    )


class MissionEventUpcaster(Protocol):
    source_version: int
    target_version: int

    def upcast(
        self,
        raw_event: Mapping[str, Any],
        *,
        context: MissionEventDeserializationContext,
    ) -> dict[str, Any]:
        ...


class MissionEventSchemaVersionError(ValueError):
    def __init__(
        self,
        *,
        schema_version: object,
        event_type: object | None,
        sequence: object | None,
    ) -> None:
        self.schema_version = schema_version
        self.event_type = event_type
        self.sequence = sequence
        super().__init__(
            "Invalid Mission event schema version "
            f"{schema_version!r} for event type {event_type!r} at "
            f"sequence {sequence!r}"
        )


class UnsupportedMissionEventSchemaVersionError(
    MissionEventSchemaVersionError
):
    def __init__(
        self,
        *,
        actual_version: int,
        current_version: int,
        event_type: object | None,
        sequence: object | None,
    ) -> None:
        self.actual_version = actual_version
        self.current_version = current_version
        super().__init__(
            schema_version=actual_version,
            event_type=event_type,
            sequence=sequence,
        )
        self.args = (
            "Mission event schema version "
            f"{actual_version} is newer than supported version "
            f"{current_version} for event type {event_type!r} at "
            f"sequence {sequence!r}",
        )


class MissingMissionEventUpcasterError(MissionEventSchemaVersionError):
    pass


class InvalidMissionEventUpcastResultError(MissionEventSchemaVersionError):
    pass


class InvalidMissionEventUpcasterRegistryError(ValueError):
    pass


class MissionEventUpcasterV0ToV1:
    source_version = LEGACY_MISSION_EVENT_SCHEMA_VERSION
    target_version = 1

    def upcast(
        self,
        raw_event: Mapping[str, Any],
        *,
        context: MissionEventDeserializationContext,
    ) -> dict[str, Any]:
        upcasted_event = dict(raw_event)
        upcasted_event["schema_version"] = self.target_version
        return upcasted_event


class MissionEventUpcasterV1ToV2:
    source_version = 1
    target_version = 2

    def upcast(
        self,
        raw_event: Mapping[str, Any],
        *,
        context: MissionEventDeserializationContext,
    ) -> dict[str, Any]:
        mission_id = context.mission_id
        if mission_id is None:
            raise MissingMissionEventDeserializationContextError(
                "mission_id is required to upcast a legacy Mission event"
            )
        sequence = raw_event.get("sequence")
        if type(sequence) is not int or sequence < 1:
            raise ValueError("Legacy Mission event sequence must be positive")
        upcasted_event = dict(raw_event)
        upcasted_event["schema_version"] = self.target_version
        if "event_id" not in upcasted_event:
            upcasted_event["event_id"] = str(
                derive_legacy_event_id(
                    mission_id=mission_id,
                    sequence=sequence,
                )
            )
        return upcasted_event


class MissionEventUpcasterV2ToV3:
    source_version = 2
    target_version = CURRENT_MISSION_EVENT_SCHEMA_VERSION

    def upcast(
        self,
        raw_event: Mapping[str, Any],
        *,
        context: MissionEventDeserializationContext,
    ) -> dict[str, Any]:
        event_id = UUID(str(raw_event["event_id"]))
        upcasted_event = dict(raw_event)
        upcasted_event["schema_version"] = self.target_version
        if "correlation_id" not in upcasted_event:
            upcasted_event["correlation_id"] = str(
                context.previous_correlation_id or event_id
            )
        if "causation_id" not in upcasted_event:
            upcasted_event["causation_id"] = (
                str(context.previous_event_id)
                if context.previous_event_id is not None
                else None
            )
        return upcasted_event


DEFAULT_MISSION_EVENT_UPCASTERS = (
    MissionEventUpcasterV0ToV1(),
    MissionEventUpcasterV1ToV2(),
    MissionEventUpcasterV2ToV3(),
)


def build_mission_event_upcaster_registry(
    upcasters: Iterable[MissionEventUpcaster],
    *,
    current_schema_version: int = CURRENT_MISSION_EVENT_SCHEMA_VERSION,
) -> Mapping[int, MissionEventUpcaster]:
    if (
        type(current_schema_version) is not int
        or current_schema_version < LEGACY_MISSION_EVENT_SCHEMA_VERSION
    ):
        raise InvalidMissionEventUpcasterRegistryError(
            "Current Mission event schema version must be a non-negative integer"
        )

    registry: dict[int, MissionEventUpcaster] = {}
    for upcaster in upcasters:
        source_version = upcaster.source_version
        target_version = upcaster.target_version
        if (
            type(source_version) is not int
            or type(target_version) is not int
            or source_version < LEGACY_MISSION_EVENT_SCHEMA_VERSION
            or target_version != source_version + 1
            or target_version > current_schema_version
        ):
            raise InvalidMissionEventUpcasterRegistryError(
                "Mission event upcasters must form adjacent schema versions"
            )
        if source_version in registry:
            raise InvalidMissionEventUpcasterRegistryError(
                "Mission event upcaster source versions must be unique"
            )
        registry[source_version] = upcaster

    expected_sources = range(
        LEGACY_MISSION_EVENT_SCHEMA_VERSION,
        current_schema_version,
    )
    if set(registry) != set(expected_sources):
        raise InvalidMissionEventUpcasterRegistryError(
            "Mission event upcasters must form a contiguous version chain"
        )
    return MappingProxyType(registry)

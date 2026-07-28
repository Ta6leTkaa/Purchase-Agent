from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.domain.execution import ExecutionEvent
from app.domain.mission import MissionType
from app.domain.provider_resolution import (
    ProviderResolutionFailedEventPayload,
    ProviderResolutionFailureReason,
    ProviderResolutionSnapshot,
    ProviderResolvedEventPayload,
    ProviderSelectionChangedEventPayload,
    ProviderSelectionMode,
)
from app.services.mission_event_serializer import (
    MissionEventSerializationError,
    PydanticMissionEventSerializer,
)
from app.services.mission_event_store import MissionJsonEventStore
from app.services.mission_event_upcaster import (
    CURRENT_MISSION_EVENT_SCHEMA_VERSION,
    InvalidMissionEventUpcasterRegistryError,
    InvalidMissionEventUpcastResultError,
    MissionEventDeserializationContext,
    MissionEventSchemaVersionError,
    MissionEventUpcasterV0ToV1,
    MissionEventUpcasterV1ToV2,
    MissionEventUpcasterV2ToV3,
    UnsupportedMissionEventSchemaVersionError,
)

CURRENT_TIME = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
CONTEXT = MissionEventDeserializationContext(
    mission_id=UUID("14dbe878-d3eb-4f66-a28a-b7904393f0c0")
)


def _provider_events() -> list[ExecutionEvent]:
    snapshot = ProviderResolutionSnapshot(
        selection_mode=ProviderSelectionMode.automatic,
        requested_provider_id=None,
        resolved_provider_id="mock_train",
        candidate_provider_ids=("mock_train",),
        mission_type=MissionType.TRAIN_TICKET,
    )
    return [
        ExecutionEvent(
            sequence=1,
            timestamp=CURRENT_TIME,
            type="provider_resolved",
            message="Provider resolved.",
            metadata=ProviderResolvedEventPayload(
                provider_id="mock_train",
                mission_type=MissionType.TRAIN_TICKET,
                selection_mode=ProviderSelectionMode.automatic,
                snapshot=snapshot,
            ).model_dump(mode="json"),
        ),
        ExecutionEvent(
            sequence=2,
            timestamp=CURRENT_TIME,
            type="provider_resolution_failed",
            message="Provider resolution failed.",
            metadata=ProviderResolutionFailedEventPayload(
                reason=ProviderResolutionFailureReason.no_supporting_provider,
                mission_type=MissionType.TRAIN_TICKET,
            ).model_dump(mode="json"),
        ),
        ExecutionEvent(
            sequence=3,
            timestamp=CURRENT_TIME,
            type="provider_selection_changed",
            message="Provider selection changed.",
            metadata=ProviderSelectionChangedEventPayload(
                previous_provider_id=None,
                new_provider_id="mock_train",
                previous_selection_mode=ProviderSelectionMode.automatic,
                new_selection_mode=ProviderSelectionMode.explicit,
            ).model_dump(mode="json"),
        ),
    ]


@pytest.mark.parametrize("event", _provider_events())
def test_provider_events_round_trip_through_serializer(
    event: ExecutionEvent,
) -> None:
    serializer = PydanticMissionEventSerializer()

    persisted = serializer.serialize(event)

    assert serializer.deserialize(persisted) == event


def test_serializer_preserves_sequence_timestamp_and_nested_snapshot() -> None:
    serializer = PydanticMissionEventSerializer()
    event = _provider_events()[0]

    persisted = serializer.serialize(event)

    assert persisted["sequence"] == 1
    assert persisted["timestamp"] == "2026-07-24T12:00:00Z"
    assert persisted["metadata"]["snapshot"] == {
        "selection_mode": "automatic",
        "requested_provider_id": None,
        "resolved_provider_id": "mock_train",
        "candidate_provider_ids": ["mock_train"],
        "mission_type": "train_ticket",
    }


def test_serializer_round_trips_provider_operation_failure_event() -> None:
    serializer = PydanticMissionEventSerializer()
    event = ExecutionEvent(
        sequence=4,
        timestamp=CURRENT_TIME,
        type="provider_operation_failed",
        message="Provider operation failed.",
        metadata={
            "provider_id": "mock_train",
            "operation": "search",
        },
    )

    persisted = serializer.serialize(event)

    assert serializer.deserialize(persisted) == event


def test_store_uses_serializer_for_mission_json_round_trip() -> None:
    store = MissionJsonEventStore(PydanticMissionEventSerializer())
    events = _provider_events()

    persisted = store.serialize(events)

    assert store.deserialize(persisted, last_event_sequence=3) == events


def test_serializer_rejects_unknown_event_type() -> None:
    serializer = PydanticMissionEventSerializer()
    event = ExecutionEvent(
        sequence=7,
        timestamp=CURRENT_TIME,
        type="unknown_event",
        message="Unknown event.",
    )

    with pytest.raises(MissionEventSerializationError) as error:
        serializer.serialize(event)

    assert error.value.event_type == "unknown_event"
    assert error.value.sequence == 7

    with pytest.raises(MissionEventSerializationError):
        serializer.deserialize(
            {
                "sequence": 7,
                "timestamp": "2026-07-24T12:00:00Z",
                "type": "unknown_event",
                "message": "Unknown event.",
                "metadata": {},
            }
        )


@pytest.mark.parametrize(
    "raw",
    [
        {
            "sequence": 1,
            "timestamp": "not-a-timestamp",
            "type": "mission_started",
            "message": "Mission started.",
            "metadata": {},
        },
        {
            "timestamp": "2026-07-24T12:00:00Z",
            "type": "mission_started",
            "message": "Mission started.",
            "metadata": {},
        },
        {
            "sequence": 1,
            "timestamp": "2026-07-24T12:00:00Z",
            "type": "provider_resolved",
            "message": "Provider resolved.",
            "metadata": {},
        },
        {
            "sequence": 1,
            "timestamp": "2026-07-24T12:00:00Z",
            "type": "provider_resolved",
            "message": "Provider resolved.",
        },
    ],
)
def test_serializer_rejects_malformed_persisted_event(
    raw: dict[str, object],
) -> None:
    serializer = PydanticMissionEventSerializer()

    with pytest.raises(MissionEventSerializationError):
        serializer.deserialize(raw)


def test_serializer_preserves_existing_extra_field_policy() -> None:
    serializer = PydanticMissionEventSerializer()
    raw = serializer.serialize(
        ExecutionEvent(
            sequence=1,
            timestamp=CURRENT_TIME,
            type="mission_started",
            message="Mission started.",
            metadata={"source": "legacy"},
        )
    )
    raw["legacy_field"] = "ignored-by-existing-schema"

    event = serializer.deserialize(raw)

    assert serializer.serialize(event) == {
        key: value
        for key, value in raw.items()
        if key != "legacy_field"
    }


def test_serializer_writes_current_schema_version() -> None:
    serializer = PydanticMissionEventSerializer()

    for event in _provider_events():
        assert serializer.serialize(event)["schema_version"] == (
            CURRENT_MISSION_EVENT_SCHEMA_VERSION
        )


def test_serializer_upcasts_legacy_event_without_mutating_it() -> None:
    serializer = PydanticMissionEventSerializer()
    source_event = _provider_events()[0]
    legacy_event = serializer.serialize(source_event)
    legacy_event.pop("schema_version")
    legacy_event["unknown_top_level"] = {"preserved": True}
    original_nested_payload = dict(legacy_event["metadata"])

    restored = serializer.deserialize(legacy_event, context=CONTEXT)
    current_event = serializer.serialize(restored)

    assert restored == source_event
    assert "schema_version" not in legacy_event
    assert legacy_event["metadata"] == original_nested_payload
    assert current_event["schema_version"] == CURRENT_MISSION_EVENT_SCHEMA_VERSION


@pytest.mark.parametrize("event", _provider_events())
def test_serializer_upcasts_all_legacy_provider_events(
    event: ExecutionEvent,
) -> None:
    serializer = PydanticMissionEventSerializer()
    legacy_event = serializer.serialize(event)
    legacy_event.pop("schema_version")

    assert serializer.deserialize(legacy_event, context=CONTEXT) == event


def test_current_schema_event_bypasses_legacy_upcaster() -> None:
    upcaster = _CountingUpcaster()
    serializer = PydanticMissionEventSerializer(
        upcasters=(
            upcaster,
            MissionEventUpcasterV1ToV2(),
            MissionEventUpcasterV2ToV3(),
        ),
    )
    source_event = _provider_events()[0]
    current_event = PydanticMissionEventSerializer().serialize(source_event)

    assert serializer.deserialize(current_event) == source_event
    assert upcaster.calls == 0


def test_store_deserializes_mixed_version_events_in_sequence_order() -> None:
    serializer = PydanticMissionEventSerializer()
    events = _provider_events()
    raw_events = [serializer.serialize(event) for event in events]
    raw_events[0].pop("schema_version")
    raw_events[1].pop("schema_version")

    restored = MissionJsonEventStore(serializer).deserialize(
        raw_events,
        last_event_sequence=3,
        mission_id=CONTEXT.mission_id,
    )

    assert restored == events


def test_serializer_rejects_future_schema_version() -> None:
    raw = PydanticMissionEventSerializer().serialize(_provider_events()[0])
    raw["schema_version"] = 999

    with pytest.raises(UnsupportedMissionEventSchemaVersionError) as error:
        PydanticMissionEventSerializer().deserialize(raw)

    assert error.value.actual_version == 999
    assert error.value.current_version == CURRENT_MISSION_EVENT_SCHEMA_VERSION
    assert error.value.event_type == "provider_resolved"
    assert error.value.sequence == 1


@pytest.mark.parametrize(
    "invalid_version",
    [None, True, False, "1", 1.0, -1, {}, []],
)
def test_serializer_rejects_invalid_schema_versions(
    invalid_version: object,
) -> None:
    raw = PydanticMissionEventSerializer().serialize(_provider_events()[0])
    raw["schema_version"] = invalid_version

    with pytest.raises(MissionEventSchemaVersionError):
        PydanticMissionEventSerializer().deserialize(raw)


@pytest.mark.parametrize(
    "result",
    [
        {},
        {"schema_version": 0},
        {"schema_version": 2},
        "not-a-mapping",
    ],
)
def test_serializer_rejects_invalid_upcaster_result(result: object) -> None:
    serializer = PydanticMissionEventSerializer(
        upcasters=(
            _InvalidResultUpcaster(result),
            MissionEventUpcasterV1ToV2(),
            MissionEventUpcasterV2ToV3(),
        ),
    )
    legacy_event = PydanticMissionEventSerializer().serialize(
        _provider_events()[0]
    )
    legacy_event.pop("schema_version")

    with pytest.raises(InvalidMissionEventUpcastResultError):
        serializer.deserialize(legacy_event, context=CONTEXT)


def test_serializer_rejects_incomplete_upcaster_registry() -> None:
    with pytest.raises(InvalidMissionEventUpcasterRegistryError):
        PydanticMissionEventSerializer(
            current_schema_version=2,
            upcasters=(MissionEventUpcasterV0ToV1(),),
        )


class _NoopUpcaster:
    def __init__(self, source_version: int, target_version: int) -> None:
        self.source_version = source_version
        self.target_version = target_version

    def upcast(
        self,
        raw_event: dict[str, object],
        **_: object,
    ) -> dict[str, object]:
        upcasted_event = dict(raw_event)
        upcasted_event["schema_version"] = self.target_version
        return upcasted_event


@pytest.mark.parametrize(
    "upcasters",
    [
        (_NoopUpcaster(0, 1), _NoopUpcaster(0, 1)),
        (_NoopUpcaster(0, 2),),
        (_NoopUpcaster(1, 2),),
    ],
)
def test_serializer_rejects_invalid_upcaster_registry(
    upcasters: tuple[object, ...],
) -> None:
    with pytest.raises(InvalidMissionEventUpcasterRegistryError):
        PydanticMissionEventSerializer(upcasters=upcasters)  # type: ignore[arg-type]


class _CountingUpcaster:
    source_version = 0
    target_version = 1

    def __init__(self) -> None:
        self.calls = 0

    def upcast(
        self,
        raw_event: dict[str, object],
        **_: object,
    ) -> dict[str, object]:
        self.calls += 1
        upcasted_event = dict(raw_event)
        upcasted_event["schema_version"] = self.target_version
        return upcasted_event


class _InvalidResultUpcaster:
    source_version = 0
    target_version = 1

    def __init__(self, result: object) -> None:
        self._result = result

    def upcast(self, raw_event: dict[str, object], **_: object) -> object:
        return self._result

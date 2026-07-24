from datetime import datetime, timezone

import pytest

from app.domain.execution import ExecutionEvent
from app.domain.mission import MissionType
from app.domain.provider_resolution import (
    ProviderResolutionFailedEventPayload,
    ProviderResolutionFailureReason,
    ProviderResolvedEventPayload,
    ProviderResolutionSnapshot,
    ProviderSelectionChangedEventPayload,
    ProviderSelectionMode,
)
from app.services.mission_event_serializer import (
    MissionEventSerializationError,
    PydanticMissionEventSerializer,
)
from app.services.mission_event_store import MissionJsonEventStore

CURRENT_TIME = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)


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
    raw = {
        "sequence": 1,
        "timestamp": "2026-07-24T12:00:00Z",
        "type": "mission_started",
        "message": "Mission started.",
        "metadata": {"source": "legacy"},
        "legacy_field": "ignored-by-existing-schema",
    }

    event = serializer.deserialize(raw)

    assert serializer.serialize(event) == {
        "sequence": 1,
        "timestamp": "2026-07-24T12:00:00Z",
        "type": "mission_started",
        "message": "Mission started.",
        "metadata": {"source": "legacy"},
    }

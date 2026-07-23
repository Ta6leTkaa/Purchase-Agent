from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domain.provider_resolution import (
    ProviderResolutionSnapshot,
    ProviderSelectionChangedEventPayload,
    ProviderResolutionFailedEventPayload,
    ProviderResolutionFailureReason,
    ProviderResolvedEventPayload,
    ProviderSelectionMode,
    create_provider_resolution_snapshot,
    create_provider_selection_changed_event,
)
from app.domain.mission import Mission, MissionType, TrainConstraints


def test_provider_resolved_payload_is_immutable_and_serializable() -> None:
    snapshot = ProviderResolutionSnapshot(
        selection_mode=ProviderSelectionMode.automatic,
        requested_provider_id=None,
        resolved_provider_id="mock_train",
        candidate_provider_ids=("mock_train",),
        mission_type=MissionType.TRAIN_TICKET,
    )
    payload = ProviderResolvedEventPayload(
        provider_id="  mock_train  ",
        mission_type=MissionType.TRAIN_TICKET,
        selection_mode=ProviderSelectionMode.automatic,
        snapshot=snapshot,
    )

    assert payload.provider_id == "mock_train"
    assert payload.model_dump(mode="json") == {
        "provider_id": "mock_train",
        "mission_type": "train_ticket",
        "selection_mode": "automatic",
        "snapshot": {
            "selection_mode": "automatic",
            "requested_provider_id": None,
            "resolved_provider_id": "mock_train",
            "candidate_provider_ids": ["mock_train"],
            "mission_type": "train_ticket",
        },
    }
    with pytest.raises(ValidationError):
        payload.provider_id = "other"


@pytest.mark.parametrize("provider_id", ["", "   "])
def test_provider_resolved_payload_rejects_empty_provider_id(
    provider_id: str,
) -> None:
    with pytest.raises(ValidationError):
        ProviderResolvedEventPayload(
            provider_id=provider_id,
            mission_type=MissionType.TRAIN_TICKET,
            selection_mode=ProviderSelectionMode.explicit,
            snapshot=ProviderResolutionSnapshot(
                selection_mode=ProviderSelectionMode.explicit,
                requested_provider_id="mock_train",
                resolved_provider_id="mock_train",
                candidate_provider_ids=("mock_train",),
                mission_type=MissionType.TRAIN_TICKET,
            ),
        )


@pytest.mark.parametrize(
    ("selection_mode", "requested_provider_id", "resolved_provider_id"),
    [
        (ProviderSelectionMode.automatic, None, "provider_a"),
        (ProviderSelectionMode.explicit, "provider_a", "provider_a"),
    ],
)
def test_provider_resolution_snapshot_is_immutable_and_serializable(
    selection_mode: ProviderSelectionMode,
    requested_provider_id: str | None,
    resolved_provider_id: str,
) -> None:
    snapshot = ProviderResolutionSnapshot(
        selection_mode=selection_mode,
        requested_provider_id=requested_provider_id,
        resolved_provider_id=resolved_provider_id,
        candidate_provider_ids=(resolved_provider_id,),
        mission_type=MissionType.TRAIN_TICKET,
    )

    assert snapshot.model_dump(mode="json")["candidate_provider_ids"] == [
        resolved_provider_id
    ]
    with pytest.raises(ValidationError):
        snapshot.resolved_provider_id = "other_provider"


@pytest.mark.parametrize(
    (
        "selection_mode",
        "requested_provider_id",
        "resolved_provider_id",
        "candidates",
    ),
    [
        (ProviderSelectionMode.automatic, None, "provider_a", ()),
        (ProviderSelectionMode.automatic, "provider_a", "provider_a", ("provider_a",)),
        (ProviderSelectionMode.explicit, None, "provider_a", ("provider_a",)),
        (ProviderSelectionMode.explicit, "provider_a", "provider_b", ("provider_b",)),
        (ProviderSelectionMode.automatic, None, "provider_a", ("provider_b",)),
    ],
)
def test_provider_resolution_snapshot_rejects_invalid_values(
    selection_mode: ProviderSelectionMode,
    requested_provider_id: str | None,
    resolved_provider_id: str,
    candidates: tuple[str, ...],
) -> None:
    with pytest.raises(ValidationError):
        ProviderResolutionSnapshot(
            selection_mode=selection_mode,
            requested_provider_id=requested_provider_id,
            resolved_provider_id=resolved_provider_id,
            candidate_provider_ids=candidates,
            mission_type=MissionType.TRAIN_TICKET,
        )


def test_provider_resolution_snapshot_factory_uses_mission_selection() -> None:
    mission = Mission(
        id=uuid4(),
        type=MissionType.TRAIN_TICKET,
        title="Moscow to Saint Petersburg",
        participant_ids=[uuid4()],
        provider="mock_train",
        provider_id="provider_a",
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Saint Petersburg",
            travel_date=date(2026, 8, 1),
            passengers_count=1,
        ),
    )

    snapshot = create_provider_resolution_snapshot(
        mission=mission,
        resolved_provider_id="provider_a",
        candidate_provider_ids=("provider_a",),
    )

    assert snapshot.selection_mode is ProviderSelectionMode.explicit
    assert snapshot.requested_provider_id == "provider_a"


def test_ambiguous_resolution_failure_payload_preserves_candidates() -> None:
    payload = ProviderResolutionFailedEventPayload(
        reason=ProviderResolutionFailureReason.ambiguous_provider,
        mission_type=MissionType.TRAIN_TICKET,
        candidate_provider_ids=("provider_b", "provider_a"),
    )

    assert payload.candidate_provider_ids == ("provider_b", "provider_a")
    assert payload.model_dump(mode="json")["reason"] == "ambiguous_provider"


def test_resolution_failure_payload_rejects_invalid_reason_combination() -> None:
    with pytest.raises(ValidationError):
        ProviderResolutionFailedEventPayload(
            reason=ProviderResolutionFailureReason.unknown_provider,
            mission_type=MissionType.TRAIN_TICKET,
        )


@pytest.mark.parametrize(
    ("previous_provider_id", "new_provider_id", "previous_mode", "new_mode"),
    [
        (
            None,
            "provider_a",
            ProviderSelectionMode.automatic,
            ProviderSelectionMode.explicit,
        ),
        (
            "provider_a",
            None,
            ProviderSelectionMode.explicit,
            ProviderSelectionMode.automatic,
        ),
        (
            "provider_a",
            "provider_b",
            ProviderSelectionMode.explicit,
            ProviderSelectionMode.explicit,
        ),
    ],
)
def test_provider_selection_changed_payload_validates_modes(
    previous_provider_id: str | None,
    new_provider_id: str | None,
    previous_mode: ProviderSelectionMode,
    new_mode: ProviderSelectionMode,
) -> None:
    payload = ProviderSelectionChangedEventPayload(
        previous_provider_id=previous_provider_id,
        new_provider_id=new_provider_id,
        previous_selection_mode=previous_mode,
        new_selection_mode=new_mode,
    )

    assert payload.model_dump(mode="json") == {
        "previous_provider_id": previous_provider_id,
        "new_provider_id": new_provider_id,
        "previous_selection_mode": previous_mode.value,
        "new_selection_mode": new_mode.value,
    }
    with pytest.raises(ValidationError):
        payload.new_provider_id = "provider_c"


@pytest.mark.parametrize(
    ("previous_provider_id", "new_provider_id", "previous_mode", "new_mode"),
    [
        (
            None,
            None,
            ProviderSelectionMode.automatic,
            ProviderSelectionMode.automatic,
        ),
        (
            "provider_a",
            "provider_a",
            ProviderSelectionMode.explicit,
            ProviderSelectionMode.explicit,
        ),
        (
            "provider_a",
            "provider_b",
            ProviderSelectionMode.automatic,
            ProviderSelectionMode.explicit,
        ),
        (
            "provider_a",
            None,
            ProviderSelectionMode.explicit,
            ProviderSelectionMode.explicit,
        ),
        (
            "",
            "provider_a",
            ProviderSelectionMode.automatic,
            ProviderSelectionMode.explicit,
        ),
        (
            "provider_a",
            "   ",
            ProviderSelectionMode.explicit,
            ProviderSelectionMode.explicit,
        ),
    ],
)
def test_provider_selection_changed_payload_rejects_invalid_values(
    previous_provider_id: str | None,
    new_provider_id: str | None,
    previous_mode: ProviderSelectionMode,
    new_mode: ProviderSelectionMode,
) -> None:
    with pytest.raises(ValidationError):
        ProviderSelectionChangedEventPayload(
            previous_provider_id=previous_provider_id,
            new_provider_id=new_provider_id,
            previous_selection_mode=previous_mode,
            new_selection_mode=new_mode,
        )


def test_provider_selection_changed_event_factory_uses_typed_payload() -> None:
    occurred_at = datetime(2026, 7, 23, 10, 0, tzinfo=timezone.utc)
    mission = make_mission()

    event = create_provider_selection_changed_event(
        mission=mission,
        previous_provider_id=None,
        new_provider_id="  provider_a  ",
        occurred_at=occurred_at,
    )

    assert event.type == "provider_selection_changed"
    assert event.timestamp == occurred_at
    assert event.sequence == 1
    assert mission.last_event_sequence == 1
    assert event.metadata == {
        "previous_provider_id": None,
        "new_provider_id": "provider_a",
        "previous_selection_mode": "automatic",
        "new_selection_mode": "explicit",
    }


def test_provider_selection_changed_event_factory_rejects_naive_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        create_provider_selection_changed_event(
            mission=make_mission(),
            previous_provider_id=None,
            new_provider_id="provider_a",
            occurred_at=datetime(2026, 7, 23, 10, 0),
        )


def make_mission() -> Mission:
    return Mission(
        id=uuid4(),
        type=MissionType.TRAIN_TICKET,
        title="Moscow to Saint Petersburg",
        participant_ids=[uuid4()],
        provider="mock_train",
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Saint Petersburg",
            travel_date=date(2026, 8, 1),
            passengers_count=1,
        ),
    )

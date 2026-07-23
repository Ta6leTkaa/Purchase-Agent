from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.domain.provider_resolution import (
    ProviderSelectionChangedEventPayload,
    ProviderResolutionFailedEventPayload,
    ProviderResolutionFailureReason,
    ProviderResolvedEventPayload,
    ProviderSelectionMode,
    create_provider_selection_changed_event,
)
from app.domain.mission import MissionType


def test_provider_resolved_payload_is_immutable_and_serializable() -> None:
    payload = ProviderResolvedEventPayload(
        provider_id="  mock_train  ",
        mission_type=MissionType.TRAIN_TICKET,
        selection_mode=ProviderSelectionMode.automatic,
    )

    assert payload.provider_id == "mock_train"
    assert payload.model_dump(mode="json") == {
        "provider_id": "mock_train",
        "mission_type": "train_ticket",
        "selection_mode": "automatic",
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
        )


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

    event = create_provider_selection_changed_event(
        previous_provider_id=None,
        new_provider_id="  provider_a  ",
        occurred_at=occurred_at,
    )

    assert event.type == "provider_selection_changed"
    assert event.timestamp == occurred_at
    assert event.metadata == {
        "previous_provider_id": None,
        "new_provider_id": "provider_a",
        "previous_selection_mode": "automatic",
        "new_selection_mode": "explicit",
    }


def test_provider_selection_changed_event_factory_rejects_naive_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        create_provider_selection_changed_event(
            previous_provider_id=None,
            new_provider_id="provider_a",
            occurred_at=datetime(2026, 7, 23, 10, 0),
        )

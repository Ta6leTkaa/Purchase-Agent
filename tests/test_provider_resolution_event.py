import pytest
from pydantic import ValidationError

from app.domain.provider_resolution import (
    ProviderResolutionFailedEventPayload,
    ProviderResolutionFailureReason,
    ProviderResolvedEventPayload,
    ProviderSelectionMode,
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

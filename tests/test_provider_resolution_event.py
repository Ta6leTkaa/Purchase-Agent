import pytest
from pydantic import ValidationError

from app.domain.provider_resolution import (
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

from enum import Enum

from pydantic import BaseModel, ConfigDict, field_validator

from app.domain.mission import MissionType
from app.domain.provider_id import normalize_provider_id


class ProviderSelectionMode(str, Enum):
    explicit = "explicit"
    automatic = "automatic"


class ProviderResolvedEventPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider_id: str
    mission_type: MissionType
    selection_mode: ProviderSelectionMode

    @field_validator("provider_id")
    @classmethod
    def validate_provider_id(cls, value: str) -> str:
        normalized_value = normalize_provider_id(value)
        assert normalized_value is not None
        return normalized_value

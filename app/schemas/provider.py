from pydantic import BaseModel, ConfigDict

from app.domain.mission import MissionExecutionMode, MissionType


class ProviderResponse(BaseModel):
    """Public, read-only description of a configured provider adapter."""

    model_config = ConfigDict(frozen=True)

    provider_id: str
    mission_types: tuple[MissionType, ...]
    execution_modes: tuple[MissionExecutionMode, ...]


class ProviderListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    providers: tuple[ProviderResponse, ...]


class SupportingProviderListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    mission_type: MissionType
    providers: tuple[ProviderResponse, ...]

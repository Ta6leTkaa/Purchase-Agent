from pydantic import BaseModel, ConfigDict

from app.domain.mission import MissionType


class ProviderCapability(BaseModel):
    """A mission type an adapter can handle without additional routing."""

    mission_type: MissionType

    model_config = ConfigDict(frozen=True)

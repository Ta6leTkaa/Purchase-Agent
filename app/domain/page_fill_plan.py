from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProfileField(StrEnum):
    FIRST_NAME = "first_name"
    LAST_NAME = "last_name"
    BIRTH_DATE = "birth_date"
    DOCUMENT_NUMBER = "document_number"


class PageFieldBinding(BaseModel):
    """A control-to-profile mapping that never contains the profile value."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    control_id: str = Field(pattern=r"^control_[1-9][0-9]*$", max_length=32)
    profile_field: ProfileField
    person_id: UUID
    sensitive: bool = False


class PageFillPlan(BaseModel):
    """A reviewable, value-free plan for filling the currently captured page."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_url: str = Field(min_length=1, max_length=2_048)
    created_at: datetime
    bindings: tuple[PageFieldBinding, ...] = Field(default=(), max_length=300)
    unmatched_required_controls: tuple[str, ...] = Field(default=(), max_length=300)

    @field_validator("created_at")
    @classmethod
    def require_aware_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("fill plan created_at must be timezone-aware")
        return value

    @field_validator("unmatched_required_controls")
    @classmethod
    def require_unique_controls(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("unmatched controls must be unique")
        return value

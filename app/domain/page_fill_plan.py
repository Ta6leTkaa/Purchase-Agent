from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ProfileField(StrEnum):
    FIRST_NAME = "first_name"
    LAST_NAME = "last_name"
    BIRTH_DATE = "birth_date"
    DOCUMENT_NUMBER = "document_number"


class IntentField(StrEnum):
    ORIGIN = "origin"
    DESTINATION = "destination"
    REQUESTED_DATE = "requested_date"
    EARLIEST_TIME = "earliest_time"
    LATEST_TIME = "latest_time"
    REQUESTED_QUANTITY = "requested_quantity"
    SEARCH_TERM = "search_term"


class PageFieldBinding(BaseModel):
    """A control-to-profile mapping that never contains the profile value."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    control_id: str = Field(pattern=r"^control_[1-9][0-9]*$", max_length=32)
    profile_field: ProfileField
    person_id: UUID
    sensitive: bool = False


class PageIntentBinding(BaseModel):
    """A control-to-intent mapping that references a structured task field."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    control_id: str = Field(pattern=r"^control_[1-9][0-9]*$", max_length=32)
    intent_field: IntentField
    search_term_index: int | None = Field(default=None, ge=0, le=19)

    @model_validator(mode="after")
    def validate_search_term_index(self) -> "PageIntentBinding":
        is_search_term = self.intent_field is IntentField.SEARCH_TERM
        if is_search_term != (self.search_term_index is not None):
            raise ValueError("search_term_index is required only for search terms")
        return self


class PageFillPlan(BaseModel):
    """A reviewable, value-free plan for filling the currently captured page."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_url: str = Field(min_length=1, max_length=2_048)
    created_at: datetime
    bindings: tuple[PageFieldBinding, ...] = Field(default=(), max_length=300)
    intent_bindings: tuple[PageIntentBinding, ...] = Field(
        default=(), max_length=300
    )
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

    @model_validator(mode="after")
    def require_one_binding_per_control(self) -> "PageFillPlan":
        control_ids = [binding.control_id for binding in self.bindings]
        control_ids.extend(binding.control_id for binding in self.intent_bindings)
        if len(set(control_ids)) != len(control_ids):
            raise ValueError("a page control can have only one binding")
        if set(control_ids) & set(self.unmatched_required_controls):
            raise ValueError("a bound control cannot also be unmatched")
        intent_keys = [
            (binding.intent_field, binding.search_term_index)
            for binding in self.intent_bindings
        ]
        if len(set(intent_keys)) != len(intent_keys):
            raise ValueError("an intent field can have only one binding")
        return self

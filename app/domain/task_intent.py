from datetime import date, time
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TaskIntentIssue(StrEnum):
    INVALID_DATE = "invalid_date"
    INVALID_TIME_WINDOW = "invalid_time_window"
    QUANTITY_MISMATCH = "quantity_does_not_match_selected_people"


class TaskIntent(BaseModel):
    """Structured, deterministic facts extracted from a user's instruction."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    origin: str | None = Field(default=None, max_length=200)
    destination: str | None = Field(default=None, max_length=200)
    requested_date: date | None = None
    earliest_time: time | None = None
    latest_time: time | None = None
    requested_quantity: int | None = Field(default=None, ge=1, le=100)
    participant_count: int = Field(ge=1, le=100)
    search_terms: tuple[str, ...] = Field(default=(), max_length=20)
    issues: tuple[TaskIntentIssue, ...] = Field(default=(), max_length=20)

    @field_validator("origin", "destination")
    @classmethod
    def normalize_place(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip(" ,.;:—-").split())
        return normalized[:200] or None

    @field_validator("search_terms")
    @classmethod
    def normalize_terms(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(" ".join(item.split())[:200] for item in value)
        return tuple(dict.fromkeys(item for item in normalized if item))

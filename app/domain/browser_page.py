from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BrowserControlKind(StrEnum):
    TEXT = "text"
    DATE = "date"
    EMAIL = "email"
    TEL = "tel"
    NUMBER = "number"
    SELECT = "select"
    RADIO = "radio"
    CHECKBOX = "checkbox"
    BUTTON = "button"
    LINK = "link"
    CLICKABLE = "clickable"
    OTHER = "other"


class BrowserPageControl(BaseModel):
    """A value-free description of one visible interactive control."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    control_id: str = Field(pattern=r"^control_[1-9][0-9]*$", max_length=32)
    kind: BrowserControlKind
    label: str = Field(min_length=1, max_length=200)
    field_name: str | None = Field(default=None, max_length=200)
    role: str | None = Field(default=None, max_length=64)
    required: bool = False
    disabled: bool = False
    options: tuple[str, ...] = Field(default=(), max_length=100)

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("control label must not be blank")
        return normalized

    @field_validator("field_name", "role")
    @classmethod
    def normalize_field_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return " ".join(value.split()) or None

    @field_validator("options")
    @classmethod
    def normalize_options(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(" ".join(item.split())[:200] for item in value)
        normalized = tuple(item for item in normalized if item)
        return tuple(dict.fromkeys(normalized))


class BrowserPageSnapshot(BaseModel):
    """A bounded DOM inventory that intentionally excludes control values."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str = Field(min_length=1, max_length=2_048)
    title: str = Field(default="Untitled page", min_length=1, max_length=300)
    captured_at: datetime
    controls: tuple[BrowserPageControl, ...] = Field(default=(), max_length=300)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        return " ".join(value.split())[:300] or "Untitled page"

    @field_validator("captured_at")
    @classmethod
    def require_aware_capture_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("snapshot captured_at must be timezone-aware")
        return value

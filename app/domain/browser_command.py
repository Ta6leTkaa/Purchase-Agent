from enum import StrEnum
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.browser_page import BrowserPageSnapshot


class FillValueSource(StrEnum):
    LITERAL = "literal"
    FIRST_NAME = "first_name"
    LAST_NAME = "last_name"
    BIRTH_DATE = "birth_date"
    DOCUMENT_NUMBER = "document_number"


class AgentFinishOutcome(StrEnum):
    READY_FOR_USER = "ready_for_user"
    GOAL_REACHED = "goal_reached"
    NO_MATCH = "no_match"
    NEEDS_USER = "needs_user"


class _Command(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ClickCommand(_Command):
    action: Literal["click"]
    control_id: str = Field(pattern=r"^control_[1-9][0-9]*$", max_length=32)


class FillCommand(_Command):
    action: Literal["fill"]
    control_id: str = Field(pattern=r"^control_[1-9][0-9]*$", max_length=32)
    value_source: FillValueSource
    literal_value: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_value_source(self) -> "FillCommand":
        if self.value_source is FillValueSource.LITERAL:
            if self.literal_value is None or not self.literal_value.strip():
                raise ValueError("literal fill requires a non-blank literal_value")
        elif self.literal_value is not None:
            raise ValueError("profile fill must not contain a literal_value")
        return self


class SelectCommand(_Command):
    action: Literal["select"]
    control_id: str = Field(pattern=r"^control_[1-9][0-9]*$", max_length=32)
    option_text: str = Field(min_length=1, max_length=200)

    @field_validator("option_text")
    @classmethod
    def normalize_option_text(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("option_text must not be blank")
        return normalized


class ScrollCommand(_Command):
    action: Literal["scroll"]
    direction: Literal["up", "down"]
    amount: int = Field(default=600, ge=100, le=2_000)


class WaitCommand(_Command):
    action: Literal["wait"]
    seconds: float = Field(default=1.0, ge=0.1, le=10.0)


class GoBackCommand(_Command):
    action: Literal["go_back"]


class OpenUrlCommand(_Command):
    action: Literal["open_url"]
    url: str = Field(min_length=1, max_length=2_048)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        normalized = value.strip()
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("url must be an absolute HTTP(S) URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("url must not contain credentials")
        if parsed.fragment:
            raise ValueError("url must not contain a fragment")
        return normalized


class AskUserCommand(_Command):
    action: Literal["ask_user"]
    question: str = Field(min_length=1, max_length=500)

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("question must not be blank")
        return normalized


class FinishCommand(_Command):
    action: Literal["finish"]
    outcome: AgentFinishOutcome
    summary: str = Field(min_length=1, max_length=500)

    @field_validator("summary")
    @classmethod
    def normalize_summary(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("summary must not be blank")
        return normalized


BrowserCommand = Annotated[
    ClickCommand
    | FillCommand
    | SelectCommand
    | ScrollCommand
    | WaitCommand
    | GoBackCommand
    | OpenUrlCommand
    | AskUserCommand
    | FinishCommand,
    Field(discriminator="action"),
]


class AgentDecision(BaseModel):
    """One bounded model decision; no executable code or hidden reasoning."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    command: BrowserCommand
    rationale: str = Field(min_length=1, max_length=500)
    expected_result: str = Field(min_length=1, max_length=500)

    @field_validator("rationale", "expected_result")
    @classmethod
    def normalize_explanation(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("decision explanations must not be blank")
        return normalized


class CommandExecutionResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    succeeded: bool
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=100)
    page_snapshot: BrowserPageSnapshot | None = None
    requires_user: bool = False

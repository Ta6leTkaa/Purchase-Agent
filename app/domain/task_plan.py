from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.task_permission import BrowserAction, BrowserActionRequest


class TaskPlanStep(BaseModel):
    """One declarative browser operation without form values or secrets."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    step_id: str = Field(min_length=1, max_length=64)
    action: BrowserAction
    summary: str = Field(min_length=1, max_length=300)
    depends_on: tuple[str, ...] = ()
    target_url: str | None = Field(default=None, max_length=2_048)
    creates_charge: bool = False
    reversible: bool = True
    requested_fields: tuple[str, ...] = Field(default=(), max_length=100)

    @field_validator("step_id")
    @classmethod
    def normalize_step_id(cls, value: str) -> str:
        normalized = (
            value.strip().casefold().replace("-", "_").replace(" ", "_")
        )
        if not normalized or not all(
            character.isalnum() or character == "_"
            for character in normalized
        ):
            raise ValueError("step_id must be a simple identifier")
        return normalized

    @field_validator("summary")
    @classmethod
    def normalize_summary(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("depends_on")
    @classmethod
    def normalize_dependencies(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(
            item.strip().casefold().replace("-", "_").replace(" ", "_")
            for item in value
        )
        if any(not item for item in normalized):
            raise ValueError("plan values must not be blank")
        if len(set(normalized)) != len(normalized):
            raise ValueError("plan values must not contain duplicates")
        return normalized

    @field_validator("requested_fields")
    @classmethod
    def normalize_requested_fields(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized):
            raise ValueError("plan values must not be blank")
        if len(set(normalized)) != len(normalized):
            raise ValueError("plan values must not contain duplicates")
        return normalized

    def to_action_request(self) -> BrowserActionRequest:
        return BrowserActionRequest(
            action=self.action,
            target_url=self.target_url,
            creates_charge=self.creates_charge,
            reversible=self.reversible,
        )


class TaskPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: UUID
    version: int = Field(default=1, ge=1)
    created_at: datetime
    steps: tuple[TaskPlanStep, ...] = Field(min_length=1, max_length=200)

    @field_validator("created_at")
    @classmethod
    def require_aware_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("plan created_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_step_graph(self) -> "TaskPlan":
        seen: set[str] = set()
        for step in self.steps:
            if step.step_id in seen:
                raise ValueError("plan step IDs must be unique")
            if step.step_id in step.depends_on:
                raise ValueError("a plan step cannot depend on itself")
            missing = set(step.depends_on) - seen
            if missing:
                raise ValueError(
                    "plan dependencies must reference earlier steps: "
                    + ", ".join(sorted(missing))
                )
            seen.add(step.step_id)
        return self


class TaskStepApproval(BaseModel):
    """A one-time approval bound to one step of one plan version."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    approval_id: UUID
    plan_version: int = Field(ge=1)
    step_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=100)
    approved_at: datetime
    consumed_at: datetime | None = None

    @field_validator("step_id")
    @classmethod
    def normalize_approval_step_id(cls, value: str) -> str:
        return TaskPlanStep.normalize_step_id(value)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if not normalized or not all(
            character.isalnum() or character == "_" for character in normalized
        ):
            raise ValueError("approval reason must be a simple identifier")
        return normalized

    @field_validator("approved_at", "consumed_at")
    @classmethod
    def require_aware_approval_time(
        cls, value: datetime | None
    ) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("approval timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_consumed_time(self) -> "TaskStepApproval":
        if self.consumed_at is not None and self.consumed_at < self.approved_at:
            raise ValueError("approval cannot be consumed before it is granted")
        return self


class TaskJournalOutcome(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    BLOCKED = "blocked"
    FAILED = "failed"


class TaskJournalEntry(BaseModel):
    """Safe operational record; intentionally has no arbitrary metadata."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: UUID
    sequence: int = Field(ge=1)
    timestamp: datetime
    step_id: str
    outcome: TaskJournalOutcome
    message: str = Field(min_length=1, max_length=500)
    reason_code: str | None = Field(default=None, max_length=100)

    @field_validator("timestamp")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("journal timestamp must be timezone-aware")
        return value

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        return " ".join(value.split())


class TaskExecutionJournal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: UUID
    entries: tuple[TaskJournalEntry, ...] = ()

    @model_validator(mode="after")
    def validate_entries(self) -> "TaskExecutionJournal":
        event_ids: set[UUID] = set()
        for expected_sequence, entry in enumerate(self.entries, start=1):
            if entry.event_id in event_ids:
                raise ValueError("journal event IDs must be unique")
            event_ids.add(entry.event_id)
            if entry.sequence != expected_sequence:
                raise ValueError("journal sequences must be contiguous")
        return self

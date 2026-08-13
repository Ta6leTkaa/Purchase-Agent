from datetime import datetime
from enum import StrEnum
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.task_permission import TaskPermissionPolicy
from app.domain.task_plan import TaskExecutionJournal, TaskPlan


class TaskStatus(StrEnum):
    DRAFT = "draft"
    READY = "ready"
    RUNNING = "running"
    MONITORING = "monitoring"
    WAITING_FOR_USER = "waiting_for_user"
    PREPARED = "prepared"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"


class UserActionReason(StrEnum):
    AUTHENTICATION_REQUIRED = "authentication_required"
    CAPTCHA_REQUIRED = "captcha_required"
    CONFIRMATION_REQUIRED = "confirmation_required"
    PAYMENT_REQUIRED = "payment_required"
    SENSITIVE_DATA_APPROVAL_REQUIRED = "sensitive_data_approval_required"


class AgentTask(BaseModel):
    """A site-agnostic user request assigned to selected people."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    version: int = Field(default=0, ge=0)
    instruction: str = Field(min_length=1, max_length=4_000)
    target_url: str = Field(min_length=1, max_length=2_048)
    person_ids: tuple[UUID, ...] = Field(min_length=1, max_length=100)
    status: TaskStatus = TaskStatus.DRAFT
    inferred_kind: str | None = Field(default=None, max_length=64)
    waiting_reason: UserActionReason | None = None
    permissions: TaskPermissionPolicy = TaskPermissionPolicy()
    plan: TaskPlan | None = None
    journal: TaskExecutionJournal | None = None
    created_at: datetime

    @field_validator("instruction")
    @classmethod
    def normalize_instruction(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("instruction must not be blank")
        return normalized

    @field_validator("target_url")
    @classmethod
    def normalize_target_url(cls, value: str) -> str:
        normalized = value.strip()
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("target_url must be an absolute HTTP(S) URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("target_url must not contain credentials")
        if parsed.fragment:
            raise ValueError("target_url must not contain a fragment")
        hostname = parsed.hostname.casefold()
        if parsed.scheme == "http" and hostname not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise ValueError("non-local target_url must use HTTPS")
        normalized_netloc = hostname
        if parsed.port is not None:
            normalized_netloc = f"{hostname}:{parsed.port}"
        return urlunsplit(
            (
                parsed.scheme,
                normalized_netloc,
                parsed.path or "/",
                parsed.query,
                "",
            )
        )

    @field_validator("person_ids")
    @classmethod
    def require_unique_people(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        if len(set(value)) != len(value):
            raise ValueError("person_ids must not contain duplicates")
        return value

    @field_validator("inferred_kind")
    @classmethod
    def normalize_inferred_kind(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().casefold().replace("-", "_")
        if not normalized or not all(
            character.isalnum() or character == "_" for character in normalized
        ):
            raise ValueError("inferred_kind must be a simple identifier")
        return normalized

    @field_validator("created_at")
    @classmethod
    def require_aware_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_waiting_reason(self) -> "AgentTask":
        waiting = self.status is TaskStatus.WAITING_FOR_USER
        if waiting != (self.waiting_reason is not None):
            raise ValueError(
                "waiting_reason is required only when status is waiting_for_user"
            )
        return self

    @model_validator(mode="after")
    def validate_execution_artifacts(self) -> "AgentTask":
        if self.plan is not None and self.plan.task_id != self.id:
            raise ValueError("plan must belong to the task")
        if self.journal is not None and self.journal.task_id != self.id:
            raise ValueError("journal must belong to the task")
        if self.journal is not None and self.plan is None:
            raise ValueError("journal requires a task plan")
        return self

    @property
    def target_origin(self) -> str:
        parsed = urlsplit(self.target_url)
        return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))

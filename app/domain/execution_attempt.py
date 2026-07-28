from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.provider_id import normalize_provider_id


class MissionExecutionAttemptStatus(StrEnum):
    processing = "processing"
    requires_confirmation = "requires_confirmation"
    completed = "completed"
    failed = "failed"
    recovered = "recovered"


class MissionExecutionAttempt(BaseModel):
    """An immutable audit record for one successful mission claim."""

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    mission_id: UUID
    attempt_number: int = Field(ge=1)
    status: MissionExecutionAttemptStatus
    claimed_at: datetime
    finished_at: datetime | None = None
    resolved_provider_id: str | None = None
    reservation_id: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "MissionExecutionAttempt":
        for value in (self.claimed_at, self.finished_at):
            if value is not None and (
                value.tzinfo is None or value.utcoffset() is None
            ):
                raise ValueError("attempt timestamps must be timezone-aware")
        if self.status is MissionExecutionAttemptStatus.processing:
            if self.finished_at is not None:
                raise ValueError("processing attempt cannot have finished_at")
        elif self.finished_at is None:
            raise ValueError("finished attempt must have finished_at")
        return self

    @model_validator(mode="after")
    def normalize_resolved_provider_id(self) -> "MissionExecutionAttempt":
        object.__setattr__(
            self,
            "resolved_provider_id",
            normalize_provider_id(self.resolved_provider_id),
        )
        return self

    @model_validator(mode="after")
    def normalize_reservation_id(self) -> "MissionExecutionAttempt":
        if self.reservation_id is not None:
            normalized_value = self.reservation_id.strip()
            if not normalized_value:
                raise ValueError("reservation_id must be a non-empty string")
            object.__setattr__(self, "reservation_id", normalized_value)
        return self

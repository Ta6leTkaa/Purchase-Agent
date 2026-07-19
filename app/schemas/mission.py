from datetime import datetime
from typing import Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.mission import (
    FallbackRules,
    Mission,
    MissionStatus,
    MissionType,
    TrainConstraints,
)
from app.services.clock import utc_now


class MissionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: MissionType
    title: str
    participant_ids: list[UUID] = Field(min_length=1)
    provider: str = Field(min_length=1)
    constraints: TrainConstraints
    fallback_rules: FallbackRules = Field(default_factory=FallbackRules)
    scheduled_at: datetime | None = None
    max_execution_attempts: int = Field(default=3, ge=1, le=100)

    @field_validator("scheduled_at")
    @classmethod
    def validate_scheduled_at(
        cls,
        scheduled_at: datetime | None,
    ) -> datetime | None:
        if scheduled_at is None:
            return None
        if scheduled_at.tzinfo is None or scheduled_at.utcoffset() is None:
            msg = "scheduled_at must be timezone-aware"
            raise ValueError(msg)
        if scheduled_at < utc_now():
            msg = "scheduled_at must not be in the past"
            raise ValueError(msg)
        return scheduled_at

    @model_validator(mode="after")
    def validate_participants(self) -> Self:
        if len(set(self.participant_ids)) != len(self.participant_ids):
            msg = "participant_ids must be unique"
            raise ValueError(msg)

        if self.constraints.passengers_count != len(self.participant_ids):
            msg = "passengers_count must match participant_ids count"
            raise ValueError(msg)

        return self

    def to_domain(self) -> Mission:
        status = (
            MissionStatus.waiting
            if self.scheduled_at is not None
            else MissionStatus.created
        )
        return Mission(
            id=uuid4(),
            type=self.type,
            title=self.title,
            status=status,
            participant_ids=self.participant_ids,
            provider=self.provider,
            constraints=self.constraints,
            fallback_rules=self.fallback_rules,
            scheduled_at=self.scheduled_at,
            max_execution_attempts=self.max_execution_attempts,
            execution_log=[],
            best_option=None,
        )

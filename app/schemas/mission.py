from datetime import date, datetime
from typing import Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.mission import (
    FallbackRules,
    Mission,
    MissionStatus,
    MissionType,
    TrainTicketMissionPayload,
    TrainConstraints,
)
from app.domain.provider_id import normalize_provider_id
from app.services.clock import utc_now


class TrainTicketMissionPayloadCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: str
    destination: str
    departure_date: date

    def to_domain(self) -> TrainTicketMissionPayload:
        return TrainTicketMissionPayload.model_validate(self.model_dump())


class MissionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str | None = None
    mission_type: MissionType = MissionType.TRAIN_TICKET
    payload: TrainTicketMissionPayloadCreate
    title: str
    participant_ids: list[UUID] = Field(min_length=1)
    provider: str = Field(min_length=1)
    provider_id: str | None = None
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

    @field_validator("provider_id")
    @classmethod
    def validate_provider_id(cls, value: str | None) -> str | None:
        return normalize_provider_id(value)

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
            type=MissionType.TRAIN_TICKET,
            mission_type=self.mission_type,
            payload=self.payload.to_domain(),
            title=self.title,
            status=status,
            participant_ids=self.participant_ids,
            provider=self.provider,
            provider_id=self.provider_id,
            constraints=self.constraints,
            fallback_rules=self.fallback_rules,
            scheduled_at=self.scheduled_at,
            max_execution_attempts=self.max_execution_attempts,
            execution_log=[],
            best_option=None,
        )

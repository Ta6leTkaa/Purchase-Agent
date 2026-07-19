from datetime import date, datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.domain.execution import ExecutionEvent
from app.domain.provider import ProviderOption


class MissionType(str, Enum):
    train_trip = "train_trip"


class MissionStatus(str, Enum):
    created = "created"
    waiting = "waiting"
    processing = "processing"
    running = "running"
    searching = "searching"
    option_found = "option_found"
    reserving = "reserving"
    requires_confirmation = "requires_confirmation"
    completed = "completed"
    failed = "failed"


class TrainConstraints(BaseModel):
    from_city: str
    to_city: str
    travel_date: date
    passengers_count: int = Field(ge=1)
    must_be_same_compartment: bool | None = None
    min_lower_berths: int | None = None
    max_total_price: int | None = None
    avoid_toilet: bool | None = None


class FallbackRules(BaseModel):
    allow_adjacent_compartments: bool | None = None
    allow_any_coupe_seats: bool | None = None
    notify_only_if_no_match: bool | None = None


class Mission(BaseModel):
    id: UUID
    type: MissionType
    title: str
    status: MissionStatus = MissionStatus.created
    participant_ids: list[UUID] = Field(min_length=1)
    provider: str = Field(min_length=1)
    constraints: TrainConstraints
    fallback_rules: FallbackRules = FallbackRules()
    scheduled_at: datetime | None = None
    claimed_at: datetime | None = None
    execution_attempts: int = Field(default=0, ge=0)
    execution_log: list[ExecutionEvent] = []
    best_option: ProviderOption | None = None

    @field_validator("scheduled_at", "claimed_at")
    @classmethod
    def validate_datetime_timezone(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            msg = "datetime value must be timezone-aware"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def validate_claimed_at_for_status(self) -> "Mission":
        if self.status is MissionStatus.processing and self.claimed_at is None:
            msg = "claimed_at is required for processing missions"
            raise ValueError(msg)
        if (
            self.status is not MissionStatus.processing
            and self.claimed_at is not None
        ):
            msg = "claimed_at is allowed only for processing missions"
            raise ValueError(msg)
        return self

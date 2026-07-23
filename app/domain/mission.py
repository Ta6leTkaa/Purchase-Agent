from datetime import date, datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.execution import ExecutionEvent
from app.domain.provider_id import normalize_provider_id
from app.domain.provider import ProviderOption


class MissionType(str, Enum):
    TRAIN_TICKET = "train_ticket"
    train_trip = "train_ticket"


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


class TrainTicketMissionPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    origin: str = Field(max_length=200)
    destination: str = Field(max_length=200)
    departure_date: date

    @field_validator("origin", "destination")
    @classmethod
    def normalize_city(cls, value: str) -> str:
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("city must not be empty")
        return normalized_value

    @model_validator(mode="after")
    def validate_distinct_cities(self) -> "TrainTicketMissionPayload":
        if self.origin.casefold() == self.destination.casefold():
            raise ValueError("origin and destination must be different")
        return self


class Mission(BaseModel):
    id: UUID
    type: MissionType = MissionType.TRAIN_TICKET
    mission_type: MissionType = Field(
        default=MissionType.TRAIN_TICKET,
        frozen=True,
    )
    payload: TrainTicketMissionPayload | None = Field(default=None, frozen=True)
    title: str
    status: MissionStatus = MissionStatus.created
    participant_ids: list[UUID] = Field(min_length=1)
    provider: str = Field(min_length=1)
    provider_id: str | None = None
    resolved_provider_id: str | None = None
    constraints: TrainConstraints
    fallback_rules: FallbackRules = FallbackRules()
    scheduled_at: datetime | None = None
    claimed_at: datetime | None = None
    execution_attempts: int = Field(default=0, ge=0)
    max_execution_attempts: int = Field(default=3, ge=1, le=100)
    execution_log: list[ExecutionEvent] = []
    best_option: ProviderOption | None = None

    @field_validator("payload", mode="before")
    @classmethod
    def require_typed_payload(
        cls,
        value: TrainTicketMissionPayload | dict[str, object] | None,
    ) -> TrainTicketMissionPayload | None:
        if isinstance(value, dict):
            raise ValueError("payload must be a TrainTicketMissionPayload")
        return value

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

    @field_validator("provider_id", "resolved_provider_id")
    @classmethod
    def validate_provider_id(cls, value: str | None) -> str | None:
        return normalize_provider_id(value)

    @model_validator(mode="after")
    def validate_claimed_at_for_status(self) -> "Mission":
        if self.payload is None:
            object.__setattr__(
                self,
                "payload",
                TrainTicketMissionPayload(
                    origin=self.constraints.from_city,
                    destination=self.constraints.to_city,
                    departure_date=self.constraints.travel_date,
                ),
            )
        if self.max_execution_attempts < self.execution_attempts:
            msg = "max_execution_attempts cannot be less than execution_attempts"
            raise ValueError(msg)
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

    @property
    def origin(self) -> str:
        """Temporary compatibility helper; prefer payload.origin."""
        assert self.payload is not None
        return self.payload.origin

    @property
    def destination(self) -> str:
        """Temporary compatibility helper; prefer payload.destination."""
        assert self.payload is not None
        return self.payload.destination

    @property
    def departure_date(self) -> date:
        """Temporary compatibility helper; prefer payload.departure_date."""
        assert self.payload is not None
        return self.payload.departure_date

    @property
    def has_exhausted_attempts(self) -> bool:
        return self.execution_attempts >= self.max_execution_attempts

    @property
    def can_change_provider_selection(self) -> bool:
        return self.status in {
            MissionStatus.created,
            MissionStatus.waiting,
        }

    def with_provider_selection(
        self,
        provider_id: str | None,
    ) -> "Mission":
        normalized_provider_id = normalize_provider_id(provider_id)
        if normalized_provider_id == self.provider_id:
            return self
        return self.model_copy(
            deep=True,
            update={
                "provider_id": normalized_provider_id,
                "resolved_provider_id": None,
            }
        )

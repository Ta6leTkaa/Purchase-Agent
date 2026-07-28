from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, field_validator, model_validator


class SeatBerth(StrEnum):
    lower = "lower"  # type: ignore[assignment]
    upper = "upper"  # type: ignore[assignment]


class Seat(BaseModel):
    carriage_number: int
    compartment_number: int
    seat_number: int
    berth: SeatBerth
    near_toilet: bool = False


class ProviderOptionType(StrEnum):
    train_option = "train_option"


class ProviderOption(BaseModel):
    id: UUID
    type: ProviderOptionType
    train_number: str
    from_city: str
    to_city: str
    departure_at: datetime
    arrival_at: datetime
    total_price: int
    seats: list[Seat]
    metadata: dict[str, Any] = {}


class ReservationResult(BaseModel):
    success: bool
    reservation_id: str | None = None
    requires_confirmation: bool = True
    message: str

    @field_validator("reservation_id")
    @classmethod
    def normalize_reservation_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("reservation_id must be a non-empty string")
        return normalized_value

    @model_validator(mode="after")
    def validate_reservation_outcome(self) -> "ReservationResult":
        if self.success and self.reservation_id is None:
            raise ValueError("successful reservation requires reservation_id")
        if not self.success and self.reservation_id is not None:
            raise ValueError("failed reservation must not include reservation_id")
        return self


class ConfirmationResult(BaseModel):
    success: bool
    message: str


class CancellationResult(BaseModel):
    success: bool
    message: str

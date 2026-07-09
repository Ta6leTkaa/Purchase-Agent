from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class SeatBerth(str, Enum):
    lower = "lower"
    upper = "upper"


class Seat(BaseModel):
    carriage_number: int
    compartment_number: int
    seat_number: int
    berth: SeatBerth
    near_toilet: bool = False


class ProviderOptionType(str, Enum):
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

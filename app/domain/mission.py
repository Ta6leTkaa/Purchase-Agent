from datetime import date
from enum import Enum
from uuid import UUID

from pydantic import BaseModel

from app.domain.execution import ExecutionEvent
from app.domain.provider import ProviderOption


class MissionType(str, Enum):
    train_trip = "train_trip"


class MissionStatus(str, Enum):
    created = "created"
    waiting = "waiting"
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
    passengers_count: int
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
    participant_ids: list[UUID]
    provider: str
    constraints: TrainConstraints
    fallback_rules: FallbackRules = FallbackRules()
    execution_log: list[ExecutionEvent] = []
    best_option: ProviderOption | None = None

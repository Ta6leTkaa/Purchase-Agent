from datetime import datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class WorkerKind(StrEnum):
    MISSION = "mission"
    NOTIFICATION = "notification"


class WorkerHeartbeat(BaseModel):
    worker_kind: WorkerKind
    instance_id: str = Field(min_length=1, max_length=255)
    started_at: datetime
    heartbeat_at: datetime
    last_success_at: datetime | None = None
    consecutive_failures: int = Field(default=0, ge=0)

    @field_validator("started_at", "heartbeat_at", "last_success_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("worker heartbeat timestamps must be timezone-aware")
        return value


class WorkerHealth(BaseModel):
    worker_kind: WorkerKind
    instance_id: str = Field(min_length=1, max_length=255)
    healthy: bool
    heartbeat_at: datetime
    heartbeat_age_seconds: float
    last_success_at: datetime | None
    consecutive_failures: int = Field(ge=0)


def evaluate_worker_health(
    heartbeat: WorkerHeartbeat,
    current_time: datetime,
    max_age: timedelta,
) -> WorkerHealth:
    if max_age <= timedelta(0):
        raise ValueError("max_age must be greater than zero")
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        raise ValueError("current_time must be timezone-aware")
    age = max(0.0, (current_time - heartbeat.heartbeat_at).total_seconds())
    return WorkerHealth(
        worker_kind=heartbeat.worker_kind,
        instance_id=heartbeat.instance_id,
        healthy=(
            age <= max_age.total_seconds()
            and heartbeat.consecutive_failures == 0
        ),
        heartbeat_at=heartbeat.heartbeat_at,
        heartbeat_age_seconds=round(age, 3),
        last_success_at=heartbeat.last_success_at,
        consecutive_failures=heartbeat.consecutive_failures,
    )

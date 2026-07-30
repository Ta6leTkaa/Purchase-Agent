from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class NotificationOutboxStatus(StrEnum):
    pending = "pending"
    processing = "processing"
    delivered = "delivered"
    failed = "failed"


class NotificationOutboxMessage(BaseModel):
    id: UUID
    mission_id: UUID
    event_id: UUID
    event_type: str
    occurred_at: datetime
    payload: dict[str, Any]
    recipient_ids: list[UUID] = Field(default_factory=list)
    status: NotificationOutboxStatus = NotificationOutboxStatus.pending
    delivery_attempts: int = Field(default=0, ge=0)
    available_at: datetime
    claimed_at: datetime | None = None
    delivered_at: datetime | None = None
    last_error: str | None = None


class NotificationOutboxStatistics(BaseModel):
    pending_count: int = Field(ge=0)
    processing_count: int = Field(ge=0)
    delivered_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    ready_count: int = Field(ge=0)
    oldest_pending_at: datetime | None = None

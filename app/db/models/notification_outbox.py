from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.identity import GUID, preferences_type
from app.domain.notification import (
    NotificationOutboxMessage,
    NotificationOutboxStatus,
)


class NotificationOutboxMessageModel(Base):
    __tablename__ = "notification_outbox"
    __table_args__ = (
        Index(
            "ix_notification_outbox_dispatch",
            "status",
            "available_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
    mission_id: Mapped[UUID] = mapped_column(
        GUID(),
        ForeignKey("missions.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_id: Mapped[UUID] = mapped_column(GUID(), unique=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        preferences_type,
        nullable=False,
    )
    recipient_ids: Mapped[list[str]] = mapped_column(
        preferences_type,
        nullable=False,
        default=list,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=NotificationOutboxStatus.pending.value,
    )
    delivery_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


def notification_outbox_from_model(
    model: NotificationOutboxMessageModel,
) -> NotificationOutboxMessage:
    return NotificationOutboxMessage(
        id=model.id,
        mission_id=model.mission_id,
        event_id=model.event_id,
        event_type=model.event_type,
        occurred_at=model.occurred_at,
        payload=model.payload,
        recipient_ids=[UUID(str(identity_id)) for identity_id in model.recipient_ids],
        status=NotificationOutboxStatus(model.status),
        delivery_attempts=model.delivery_attempts,
        available_at=model.available_at,
        claimed_at=model.claimed_at,
        delivered_at=model.delivered_at,
        last_error=model.last_error,
    )

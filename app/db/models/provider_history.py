from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.identity import GUID, preferences_type


class MissionProviderHistoryEventModel(Base):
    __tablename__ = "mission_provider_history_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ("
            "'provider_selection_changed', "
            "'provider_resolved', "
            "'provider_resolution_failed'"
            ")",
            name="ck_mission_provider_history_event_type",
        ),
        Index(
            "ix_mission_provider_history_events_occurred_at_sequence",
            "mission_id",
            "occurred_at",
            "legacy_event_index",
        ),
    )

    mission_id: Mapped[UUID] = mapped_column(
        GUID(),
        ForeignKey("missions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(preferences_type, "sqlite"),
        nullable=False,
    )
    legacy_event_index: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.identity import GUID, preferences_type


class MissionEventModel(Base):
    __tablename__ = "mission_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_mission_events_event_id"),
        Index(
            "ix_mission_events_mission_sequence",
            "mission_id",
            "sequence",
        ),
    )

    mission_id: Mapped[UUID] = mapped_column(
        GUID(),
        ForeignKey("missions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[UUID] = mapped_column(GUID(), nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    event: Mapped[dict[str, Any]] = mapped_column(
        preferences_type,
        nullable=False,
    )

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.identity import GUID
from app.db.models.mission import AwareDateTime


class MissionExecutionAttemptModel(Base):
    __tablename__ = "mission_execution_attempts"
    __table_args__ = (
        UniqueConstraint(
            "mission_id",
            "attempt_number",
            name="uq_mission_execution_attempt_number",
        ),
        Index(
            "ix_mission_execution_attempts_mission_claimed_at",
            "mission_id",
            "claimed_at",
        ),
        Index(
            "uq_mission_execution_attempts_one_open",
            "mission_id",
            unique=True,
            postgresql_where=text("status = 'processing'"),
            sqlite_where=text("status = 'processing'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
    mission_id: Mapped[UUID] = mapped_column(
        GUID(),
        ForeignKey("missions.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    claimed_at: Mapped[datetime] = mapped_column(
        AwareDateTime(),
        nullable=False,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        AwareDateTime(),
        nullable=True,
    )
    resolved_provider_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

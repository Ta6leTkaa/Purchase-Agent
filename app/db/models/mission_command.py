from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.identity import GUID


class MissionCommandReceiptModel(Base):
    __tablename__ = "mission_command_receipts"

    idempotency_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    mission_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("missions.id", ondelete="CASCADE"), nullable=False
    )
    command: Mapped[str] = mapped_column(String(32), nullable=False)
    result_mission_id: Mapped[UUID | None] = mapped_column(GUID(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

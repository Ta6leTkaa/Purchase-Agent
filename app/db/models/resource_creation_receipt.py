from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.identity import GUID


class ResourceCreationReceiptModel(Base):
    __tablename__ = "resource_creation_receipts"
    __table_args__ = (
        Index(
            "ix_resource_creation_receipts_retention",
            "created_at",
            "scope",
            "idempotency_key",
            postgresql_where=text("resource_id IS NOT NULL"),
        ),
    )

    scope: Mapped[str] = mapped_column(String(32), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[UUID | None] = mapped_column(GUID(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

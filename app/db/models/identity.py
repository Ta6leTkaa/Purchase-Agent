from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Date, DateTime, ForeignKey, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.domain.identity import (
    Document,
    DocumentType,
    Identity,
    Preferences,
)


class IdentityModel(Base):
    __tablename__ = "identities"

    id: Mapped[UUID] = mapped_column(PostgresUUID(as_uuid=True), primary_key=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    first_name: Mapped[str] = mapped_column(String, nullable=False)
    last_name: Mapped[str] = mapped_column(String, nullable=False)
    birth_date: Mapped[date] = mapped_column(Date, nullable=False)
    preferences: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    documents: Mapped[list["DocumentModel"]] = relationship(
        back_populates="identity",
        cascade="all, delete-orphan",
    )


class DocumentModel(Base):
    __tablename__ = "documents"

    id: Mapped[UUID] = mapped_column(PostgresUUID(as_uuid=True), primary_key=True)
    identity_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("identities.id"),
        nullable=False,
        index=True,
    )
    type: Mapped[str] = mapped_column(String, nullable=False)
    number: Mapped[str] = mapped_column(String, nullable=False)
    expires_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    identity: Mapped[IdentityModel] = relationship(back_populates="documents")


def identity_to_model(identity: Identity) -> IdentityModel:
    return IdentityModel(
        id=identity.id,
        display_name=identity.display_name,
        first_name=identity.first_name,
        last_name=identity.last_name,
        birth_date=identity.birth_date,
        preferences=identity.preferences.model_dump(mode="json"),
        documents=[
            DocumentModel(
                id=document.id,
                type=document.type.value,
                number=document.number,
                expires_at=document.expires_at,
            )
            for document in identity.documents
        ],
    )


def identity_from_model(model: IdentityModel) -> Identity:
    return Identity(
        id=model.id,
        display_name=model.display_name,
        first_name=model.first_name,
        last_name=model.last_name,
        birth_date=model.birth_date,
        documents=[
            Document(
                id=document.id,
                type=DocumentType(document.type),
                number=document.number,
                expires_at=document.expires_at,
            )
            for document in model.documents
        ],
        preferences=Preferences.model_validate(model.preferences),
    )

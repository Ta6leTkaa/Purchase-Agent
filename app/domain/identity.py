from datetime import date
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, field_validator


class DocumentType(StrEnum):
    internal_passport = "internal_passport"
    international_passport = "international_passport"
    birth_certificate = "birth_certificate"


class Document(BaseModel):
    id: UUID
    type: DocumentType
    number: str
    expires_at: date | None = None


class TrainPreferences(BaseModel):
    prefers_lower_berth: bool | None = None
    avoid_toilet: bool | None = None
    prefer_same_compartment: bool | None = None


class NotificationChannel(StrEnum):
    webhook = "webhook"
    email = "email"
    telegram = "telegram"


class NotificationPreferences(BaseModel):
    enabled: bool = True
    channels: set[NotificationChannel] = {NotificationChannel.webhook}
    external_recipient_id: str | None = None

    @field_validator("external_recipient_id")
    @classmethod
    def normalize_external_recipient_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("external_recipient_id must not be blank")
        return normalized


class Preferences(BaseModel):
    train: TrainPreferences = TrainPreferences()
    notifications: NotificationPreferences = NotificationPreferences()


class Identity(BaseModel):
    id: UUID
    display_name: str
    first_name: str
    last_name: str
    birth_date: date
    documents: list[Document] = []
    preferences: Preferences = Preferences()


class IdentitySummary(BaseModel):
    id: UUID
    display_name: str

    @classmethod
    def from_identity(cls, identity: Identity) -> "IdentitySummary":
        return cls(id=identity.id, display_name=identity.display_name)

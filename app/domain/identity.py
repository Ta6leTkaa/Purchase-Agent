from datetime import date
from enum import Enum
from uuid import UUID

from pydantic import BaseModel


class DocumentType(str, Enum):
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


class Preferences(BaseModel):
    train: TrainPreferences = TrainPreferences()


class Identity(BaseModel):
    id: UUID
    display_name: str
    first_name: str
    last_name: str
    birth_date: date
    documents: list[Document] = []
    preferences: Preferences = Preferences()

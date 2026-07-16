from datetime import date
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.domain.identity import (
    Document,
    DocumentType,
    Identity,
    Preferences,
)


class DocumentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: DocumentType
    number: str
    expires_at: date | None = None

    def to_domain(self) -> Document:
        return Document(
            id=uuid4(),
            type=self.type,
            number=self.number,
            expires_at=self.expires_at,
        )


class IdentityCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str
    first_name: str
    last_name: str
    birth_date: date
    documents: list[DocumentCreate] = Field(default_factory=list)
    preferences: Preferences = Field(default_factory=Preferences)

    def to_domain(self) -> Identity:
        return Identity(
            id=uuid4(),
            display_name=self.display_name,
            first_name=self.first_name,
            last_name=self.last_name,
            birth_date=self.birth_date,
            documents=[
                document.to_domain()
                for document in self.documents
            ],
            preferences=self.preferences,
        )

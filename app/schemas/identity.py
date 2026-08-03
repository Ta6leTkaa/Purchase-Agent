from datetime import date
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.domain.identity import (
    Document,
    DocumentType,
    Identity,
    Preferences,
)


class DocumentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: DocumentType
    number: str = Field(min_length=1, max_length=100)
    expires_at: date | None = None

    @field_validator("number")
    @classmethod
    def normalize_number(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("document number must not be blank")
        return normalized

    def to_domain(self) -> Document:
        return Document(
            id=uuid4(),
            type=self.type,
            number=self.number,
            expires_at=self.expires_at,
        )


class IdentityCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=200)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    birth_date: date
    documents: list[DocumentCreate] = Field(default_factory=list)
    preferences: Preferences = Field(default_factory=Preferences)

    @field_validator("display_name", "first_name", "last_name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("name must not be blank")
        return normalized

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


class IdentityPreferencesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preferences: Preferences


class IdentityUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    birth_date: date | None = None
    documents: list[DocumentCreate] | None = None

    @field_validator("display_name", "first_name", "last_name")
    @classmethod
    def normalize_optional_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("name must not be blank")
        return normalized

    @model_validator(mode="after")
    def require_changes(self) -> "IdentityUpdate":
        if not self.model_fields_set:
            raise ValueError("at least one identity field must be provided")
        if any(
            getattr(self, field_name) is None
            for field_name in self.model_fields_set
        ):
            raise ValueError("identity fields must not be null")
        return self

    def apply(self, identity: Identity) -> Identity:
        changes = self.model_dump(exclude_unset=True, exclude={"documents"})
        if self.documents is not None:
            changes["documents"] = [
                document.to_domain() for document in self.documents
            ]
        return identity.model_copy(update=changes)

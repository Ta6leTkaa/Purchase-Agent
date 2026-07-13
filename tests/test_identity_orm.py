from datetime import date
from uuid import uuid4

from app.db.base import Base
from app.db.models.identity import (
    identity_from_model,
    identity_to_model,
)
from app.domain.identity import (
    Document,
    DocumentType,
    Identity,
    Preferences,
    TrainPreferences,
)


def test_metadata_contains_identity_tables() -> None:
    assert "identities" in Base.metadata.tables
    assert "documents" in Base.metadata.tables


def test_documents_table_has_identity_foreign_key() -> None:
    documents_table = Base.metadata.tables["documents"]
    foreign_keys = {
        str(foreign_key.column)
        for foreign_key in documents_table.foreign_keys
    }

    assert "identities.id" in foreign_keys


def test_identity_to_model_converts_domain_identity() -> None:
    identity = make_identity()

    model = identity_to_model(identity)

    assert model.id == identity.id
    assert model.display_name == identity.display_name
    assert model.documents[0].number == "1234567890"
    assert model.preferences["train"]["prefers_lower_berth"] is True


def test_identity_from_model_restores_domain_identity() -> None:
    identity = make_identity()
    model = identity_to_model(identity)

    restored_identity = identity_from_model(model)

    assert restored_identity == identity


def test_documents_and_preferences_survive_round_trip() -> None:
    identity = make_identity()
    model = identity_to_model(identity)

    restored_identity = identity_from_model(model)

    assert len(restored_identity.documents) == 1
    assert restored_identity.documents[0].type is DocumentType.internal_passport
    assert restored_identity.preferences.train.prefers_lower_berth is True
    assert restored_identity.preferences.train.avoid_toilet is True


def make_identity() -> Identity:
    return Identity(
        id=uuid4(),
        display_name="Ivan Petrov",
        first_name="Ivan",
        last_name="Petrov",
        birth_date=date(1990, 1, 1),
        documents=[
            Document(
                id=uuid4(),
                type=DocumentType.internal_passport,
                number="1234567890",
                expires_at=date(2030, 1, 1),
            )
        ],
        preferences=Preferences(
            train=TrainPreferences(
                prefers_lower_berth=True,
                avoid_toilet=True,
                prefer_same_compartment=True,
            )
        ),
    )

from datetime import date
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.domain.identity import (
    Document,
    DocumentType,
    Identity,
    Preferences,
    TrainPreferences,
)
from app.repositories.sqlalchemy.identity import SqlAlchemyIdentityRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_create_saves_identity(test_session: AsyncSession) -> None:
    repository = SqlAlchemyIdentityRepository(test_session)
    identity = make_identity()

    created_identity = await repository.create(identity)
    loaded_identity = await repository.get(identity.id)

    assert created_identity == identity
    assert loaded_identity == identity


async def test_get_returns_identity_with_documents(
    test_session: AsyncSession,
) -> None:
    repository = SqlAlchemyIdentityRepository(test_session)
    identity = make_identity()
    await repository.create(identity)

    loaded_identity = await repository.get(identity.id)

    assert loaded_identity is not None
    assert loaded_identity.documents == identity.documents


async def test_list_returns_multiple_identities(
    test_session: AsyncSession,
) -> None:
    repository = SqlAlchemyIdentityRepository(test_session)
    first_identity = make_identity()
    second_identity = make_identity()
    await repository.create(first_identity)
    await repository.create(second_identity)

    identities = await repository.list()

    assert {identity.id for identity in identities} == {
        first_identity.id,
        second_identity.id,
    }


async def test_preferences_are_persisted_and_restored(
    test_session: AsyncSession,
) -> None:
    repository = SqlAlchemyIdentityRepository(test_session)
    identity = make_identity()
    await repository.create(identity)

    loaded_identity = await repository.get(identity.id)

    assert loaded_identity is not None
    assert loaded_identity.preferences == identity.preferences
    assert loaded_identity.preferences.train.avoid_toilet is True


async def test_clear_deletes_all_identities(test_session: AsyncSession) -> None:
    repository = SqlAlchemyIdentityRepository(test_session)
    await repository.create(make_identity())
    await repository.create(make_identity())

    await repository.clear()

    assert await repository.list() == []


async def test_data_is_available_in_new_session_after_external_commit(
    test_engine: AsyncEngine,
    clean_database: None,
) -> None:
    session_maker = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    identity = make_identity()

    async with session_maker() as session:
        repository = SqlAlchemyIdentityRepository(session)
        await repository.create(identity)
        await session.commit()

    async with session_maker() as session:
        repository = SqlAlchemyIdentityRepository(session)
        loaded_identity = await repository.get(identity.id)

    assert loaded_identity == identity


async def test_repository_does_not_commit_without_external_commit(
    test_engine: AsyncEngine,
    clean_database: None,
) -> None:
    session_maker = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    identity = make_identity()

    async with session_maker() as session:
        repository = SqlAlchemyIdentityRepository(session)
        await repository.create(identity)

    async with session_maker() as session:
        repository = SqlAlchemyIdentityRepository(session)
        loaded_identity = await repository.get(identity.id)

    assert loaded_identity is None


def make_identity(identity_id: UUID | None = None) -> Identity:
    return Identity(
        id=identity_id or uuid4(),
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

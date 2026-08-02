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
from app.services.identity_pagination import IdentityCursor

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


async def test_list_searches_names_and_applies_limit(
    test_session: AsyncSession,
) -> None:
    repository = SqlAlchemyIdentityRepository(test_session)
    matching = make_identity().model_copy(
        update={"display_name": "Anna 100% Sidorova", "first_name": "Anna"}
    )
    other = make_identity().model_copy(
        update={"display_name": "Ivan Petrov"}
    )
    await repository.create(matching)
    await repository.create(other)

    by_name = await repository.list(query="aNnA", limit=1)
    literal_wildcard = await repository.list(query="100%", limit=10)

    assert [identity.id for identity in by_name] == [matching.id]
    assert [identity.id for identity in literal_wildcard] == [matching.id]


async def test_list_summaries_projects_searchable_identity_fields(
    test_session: AsyncSession,
) -> None:
    repository = SqlAlchemyIdentityRepository(test_session)
    matching = make_identity().model_copy(
        update={"display_name": "Anna 100% Sidorova", "first_name": "Anna"}
    )
    await repository.create(matching)
    await repository.create(make_identity())

    summaries = await repository.list_summaries(query="100%", limit=1)

    assert [summary.model_dump() for summary in summaries] == [
        {"id": matching.id, "display_name": "Anna 100% Sidorova"}
    ]


async def test_list_summary_page_candidates_use_exclusive_id_cursor(
    test_session: AsyncSession,
) -> None:
    repository = SqlAlchemyIdentityRepository(test_session)
    identity_ids = sorted([uuid4(), uuid4(), uuid4()])
    for identity_id in identity_ids:
        await repository.create(make_identity(identity_id))

    first_page = await repository.list_summary_page_candidates(limit=2)
    second_page = await repository.list_summary_page_candidates(
        cursor=IdentityCursor(identity_id=first_page[-1].id),
        limit=2,
    )

    assert [item.id for item in first_page] == identity_ids[:2]
    assert [item.id for item in second_page] == identity_ids[2:]


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

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date
from uuid import uuid4

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.domain.identity import (
    Document,
    DocumentType,
    Identity,
    Preferences,
    TrainPreferences,
)
from app.repositories.sqlalchemy.identity import SqlAlchemyIdentityRepository


def test_create_saves_identity() -> None:
    async def scenario() -> None:
        await with_repository(
            lambda repository, session: _create_saves_identity(repository)
        )

    asyncio.run(scenario())


def test_get_returns_identity_with_documents_and_preferences() -> None:
    async def scenario() -> None:
        await with_repository(
            lambda repository, session: _get_returns_full_identity(repository)
        )

    asyncio.run(scenario())


def test_list_returns_multiple_identities() -> None:
    async def scenario() -> None:
        await with_repository(
            lambda repository, session: _list_returns_multiple(repository)
        )

    asyncio.run(scenario())


def test_get_unknown_identity_returns_none() -> None:
    async def scenario() -> None:
        await with_repository(
            lambda repository, session: _get_unknown_returns_none(repository)
        )

    asyncio.run(scenario())


def test_clear_deletes_data() -> None:
    async def scenario() -> None:
        await with_repository(
            lambda repository, session: _clear_deletes_data(repository)
        )

    asyncio.run(scenario())


def test_repository_does_not_commit() -> None:
    async def scenario() -> None:
        engine = create_test_engine()
        try:
            await create_tables(engine)
            session_maker = async_sessionmaker(
                engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )
            identity = make_identity()

            async with session_maker() as session:
                repository = SqlAlchemyIdentityRepository(session)
                await repository.create(identity)

            async with session_maker() as session:
                repository = SqlAlchemyIdentityRepository(session)
                assert await repository.get(identity.id) is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


async def with_repository(
    callback: Callable[
        [SqlAlchemyIdentityRepository, AsyncSession],
        Awaitable[None],
    ],
) -> None:
    engine = create_test_engine()
    try:
        await create_tables(engine)
        session_maker = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        async with session_maker() as session:
            repository = SqlAlchemyIdentityRepository(session)
            await callback(repository, session)
    finally:
        await engine.dispose()


def create_test_engine() -> AsyncEngine:
    return create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


async def create_tables(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def _create_saves_identity(
    repository: SqlAlchemyIdentityRepository,
) -> None:
    identity = make_identity()

    created_identity = await repository.create(identity)
    loaded_identity = await repository.get(identity.id)

    assert created_identity == identity
    assert loaded_identity == identity


async def _get_returns_full_identity(
    repository: SqlAlchemyIdentityRepository,
) -> None:
    identity = make_identity()
    await repository.create(identity)

    loaded_identity = await repository.get(identity.id)

    assert loaded_identity is not None
    assert loaded_identity == identity
    assert loaded_identity.documents[0].number == "1234567890"
    assert loaded_identity.preferences.train.avoid_toilet is True


async def _list_returns_multiple(
    repository: SqlAlchemyIdentityRepository,
) -> None:
    first_identity = make_identity()
    second_identity = make_identity()
    await repository.create(first_identity)
    await repository.create(second_identity)

    identities = await repository.list()

    assert {identity.id for identity in identities} == {
        first_identity.id,
        second_identity.id,
    }


async def _get_unknown_returns_none(
    repository: SqlAlchemyIdentityRepository,
) -> None:
    assert await repository.get(uuid4()) is None


async def _clear_deletes_data(
    repository: SqlAlchemyIdentityRepository,
) -> None:
    identity = make_identity()
    await repository.create(identity)

    await repository.clear()

    assert await repository.list() == []


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

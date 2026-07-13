import asyncio
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.domain.execution import ExecutionEvent
from app.domain.mission import (
    FallbackRules,
    Mission,
    MissionStatus,
    MissionType,
    TrainConstraints,
)
from app.domain.provider import (
    ProviderOption,
    ProviderOptionType,
    Seat,
    SeatBerth,
)
from app.repositories.sqlalchemy.mission import SqlAlchemyMissionRepository


def test_create_saves_mission() -> None:
    async def scenario() -> None:
        await with_repository(
            lambda repository, session: _create_saves_mission(repository)
        )

    asyncio.run(scenario())


def test_get_returns_mission() -> None:
    async def scenario() -> None:
        await with_repository(
            lambda repository, session: _get_returns_mission(repository)
        )

    asyncio.run(scenario())


def test_list_returns_multiple_missions() -> None:
    async def scenario() -> None:
        await with_repository(
            lambda repository, session: _list_returns_multiple(repository)
        )

    asyncio.run(scenario())


def test_update_saves_new_status() -> None:
    async def scenario() -> None:
        await with_repository(
            lambda repository, session: _update_saves_new_status(repository)
        )

    asyncio.run(scenario())


def test_update_saves_execution_log() -> None:
    async def scenario() -> None:
        await with_repository(
            lambda repository, session: _update_saves_execution_log(repository)
        )

    asyncio.run(scenario())


def test_update_saves_best_option() -> None:
    async def scenario() -> None:
        await with_repository(
            lambda repository, session: _update_saves_best_option(repository)
        )

    asyncio.run(scenario())


def test_get_unknown_mission_returns_none() -> None:
    async def scenario() -> None:
        await with_repository(
            lambda repository, session: _get_unknown_returns_none(repository)
        )

    asyncio.run(scenario())


def test_clear_deletes_missions() -> None:
    async def scenario() -> None:
        await with_repository(
            lambda repository, session: _clear_deletes_missions(repository)
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
            mission = make_mission()

            async with session_maker() as session:
                repository = SqlAlchemyMissionRepository(session)
                await repository.create(mission)

            async with session_maker() as session:
                repository = SqlAlchemyMissionRepository(session)
                assert await repository.get(mission.id) is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


async def with_repository(
    callback: Callable[
        [SqlAlchemyMissionRepository, AsyncSession],
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
            repository = SqlAlchemyMissionRepository(session)
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


async def _create_saves_mission(
    repository: SqlAlchemyMissionRepository,
) -> None:
    mission = make_mission()

    created_mission = await repository.create(mission)
    loaded_mission = await repository.get(mission.id)

    assert created_mission == mission
    assert loaded_mission == mission


async def _get_returns_mission(
    repository: SqlAlchemyMissionRepository,
) -> None:
    mission = make_mission()
    await repository.create(mission)

    loaded_mission = await repository.get(mission.id)

    assert loaded_mission == mission


async def _list_returns_multiple(
    repository: SqlAlchemyMissionRepository,
) -> None:
    first_mission = make_mission()
    second_mission = make_mission()
    await repository.create(first_mission)
    await repository.create(second_mission)

    missions = await repository.list()

    assert {mission.id for mission in missions} == {
        first_mission.id,
        second_mission.id,
    }


async def _update_saves_new_status(
    repository: SqlAlchemyMissionRepository,
) -> None:
    mission = make_mission()
    await repository.create(mission)
    mission.status = MissionStatus.completed

    updated_mission = await repository.update(mission)
    loaded_mission = await repository.get(mission.id)

    assert updated_mission.status is MissionStatus.completed
    assert loaded_mission is not None
    assert loaded_mission.status is MissionStatus.completed


async def _update_saves_execution_log(
    repository: SqlAlchemyMissionRepository,
) -> None:
    mission = make_mission()
    await repository.create(mission)
    mission.execution_log.append(
        ExecutionEvent(
            timestamp=datetime(2026, 7, 13, 11, 0),
            type="mission_completed",
            message="Mission completed.",
        )
    )

    updated_mission = await repository.update(mission)

    assert updated_mission.execution_log[-1].type == "mission_completed"


async def _update_saves_best_option(
    repository: SqlAlchemyMissionRepository,
) -> None:
    mission = make_mission(best_option=None)
    await repository.create(mission)
    mission.best_option = make_provider_option()

    updated_mission = await repository.update(mission)

    assert updated_mission.best_option is not None
    assert updated_mission.best_option.train_number == "001A"


async def _get_unknown_returns_none(
    repository: SqlAlchemyMissionRepository,
) -> None:
    assert await repository.get(uuid4()) is None


async def _clear_deletes_missions(
    repository: SqlAlchemyMissionRepository,
) -> None:
    await repository.create(make_mission())
    await repository.create(make_mission())

    await repository.clear()

    assert await repository.list() == []


def make_mission(
    best_option: ProviderOption | None = None,
) -> Mission:
    return Mission(
        id=uuid4(),
        type=MissionType.train_trip,
        title="Moscow to Saint Petersburg",
        status=MissionStatus.created,
        participant_ids=[uuid4(), uuid4()],
        provider="mock_train",
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Saint Petersburg",
            travel_date=date(2026, 8, 1),
            passengers_count=2,
            must_be_same_compartment=True,
            min_lower_berths=1,
            max_total_price=30000,
            avoid_toilet=True,
        ),
        fallback_rules=FallbackRules(
            allow_adjacent_compartments=True,
        ),
        execution_log=[
            ExecutionEvent(
                timestamp=datetime(2026, 7, 13, 10, 0),
                type="mission_started",
                message="Mission started.",
            )
        ],
        best_option=best_option,
    )


def make_provider_option() -> ProviderOption:
    return ProviderOption(
        id=uuid4(),
        type=ProviderOptionType.train_option,
        train_number="001A",
        from_city="Moscow",
        to_city="Saint Petersburg",
        departure_at=datetime(2026, 8, 1, 20, 0),
        arrival_at=datetime(2026, 8, 2, 6, 0),
        total_price=14200,
        seats=[
            Seat(
                carriage_number=1,
                compartment_number=1,
                seat_number=1,
                berth=SeatBerth.lower,
            )
        ],
    )

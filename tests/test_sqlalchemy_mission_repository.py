import asyncio
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.adapters.mock_train import MockTrainAdapter
from app.adapters.registry import ProviderRegistry
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
from app.repositories.mission import InvalidRepositoryTimeError
from app.repositories.sqlalchemy.mission import SqlAlchemyMissionRepository
from app.services.mission_provider_selection import SetMissionProvider


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


def test_provider_id_survives_round_trip_after_external_commit() -> None:
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
            mission.provider_id = "mock_train"

            async with session_maker() as session:
                repository = SqlAlchemyMissionRepository(session)
                await repository.create(mission)
                await session.commit()

            async with session_maker() as session:
                repository = SqlAlchemyMissionRepository(session)
                loaded_mission = await repository.get(mission.id)

            assert loaded_mission is not None
            assert loaded_mission.provider_id == "mock_train"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_provider_selection_update_survives_round_trip_after_commit() -> None:
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
            mission.resolved_provider_id = "previous_provider"

            async with session_maker() as session:
                repository = SqlAlchemyMissionRepository(session)
                await repository.create(mission)
                updated = await SetMissionProvider(
                    repository,
                    ProviderRegistry([MockTrainAdapter()]),
                ).execute(mission.id, "mock_train")
                await session.commit()

            async with session_maker() as session:
                repository = SqlAlchemyMissionRepository(session)
                loaded_mission = await repository.get(mission.id)

            assert updated.provider_id == "mock_train"
            assert updated.resolved_provider_id is None
            assert loaded_mission is not None
            assert loaded_mission.provider_id == "mock_train"
            assert loaded_mission.resolved_provider_id is None
            assert loaded_mission.status is MissionStatus.created
            assert loaded_mission.execution_attempts == 0
            assert loaded_mission.payload == mission.payload
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_list_due_filters_orders_and_limits_missions() -> None:
    async def scenario() -> None:
        await with_repository(
            lambda repository, session: _list_due_filters_orders_and_limits(
                repository
            )
        )

    asyncio.run(scenario())


def test_list_due_rejects_invalid_arguments() -> None:
    async def scenario() -> None:
        await with_repository(
            lambda repository, session: _list_due_rejects_invalid_arguments(
                repository
            )
        )

    asyncio.run(scenario())


def test_list_due_returns_committed_data_from_new_session() -> None:
    async def scenario() -> None:
        engine = create_test_engine()
        try:
            await create_tables(engine)
            session_maker = async_sessionmaker(
                engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )
            current_time = aware_datetime()
            due_mission = make_mission(
                status=MissionStatus.waiting,
                scheduled_at=current_time,
            )

            async with session_maker() as session:
                repository = SqlAlchemyMissionRepository(session)
                await repository.create(due_mission)
                await session.commit()

            async with session_maker() as session:
                repository = SqlAlchemyMissionRepository(session)
                due_missions = await repository.list_due(current_time)

            assert [mission.id for mission in due_missions] == [due_mission.id]
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_claim_due_filters_orders_limits_and_sets_processing() -> None:
    async def scenario() -> None:
        await with_repository(
            lambda repository, session: _claim_due_filters_orders_limits(
                repository
            )
        )

    asyncio.run(scenario())


def test_claim_due_does_not_claim_same_mission_twice() -> None:
    async def scenario() -> None:
        await with_repository(
            lambda repository, session: _claim_due_does_not_claim_twice(
                repository
            )
        )

    asyncio.run(scenario())


def test_claim_due_skips_missions_with_exhausted_attempts() -> None:
    async def scenario() -> None:
        await with_repository(
            lambda repository, session: _claim_due_skips_exhausted(repository)
        )

    asyncio.run(scenario())


def test_claim_due_commits_processing_status() -> None:
    async def scenario() -> None:
        engine = create_test_engine()
        try:
            await create_tables(engine)
            session_maker = async_sessionmaker(
                engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )
            current_time = aware_datetime()
            mission = make_mission(
                status=MissionStatus.waiting,
                scheduled_at=current_time,
            )

            async with session_maker() as session:
                repository = SqlAlchemyMissionRepository(session)
                await repository.create(mission)
                await session.commit()

            async with session_maker() as session:
                repository = SqlAlchemyMissionRepository(session)
                claimed_missions = await repository.claim_due(current_time)

            async with session_maker() as session:
                repository = SqlAlchemyMissionRepository(session)
                loaded_mission = await repository.get(mission.id)

            assert [mission.id for mission in claimed_missions] == [
                mission.id
            ]
            assert loaded_mission is not None
            assert loaded_mission.status is MissionStatus.processing
            assert loaded_mission.claimed_at == current_time
            assert loaded_mission.execution_attempts == 1
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
    assert created_mission.execution_attempts == 0


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
    mission.provider_id = "mock_train"

    updated_mission = await repository.update(mission)
    loaded_mission = await repository.get(mission.id)

    assert updated_mission.status is MissionStatus.completed
    assert loaded_mission is not None
    assert loaded_mission.status is MissionStatus.completed
    assert loaded_mission.provider_id == "mock_train"


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


async def _list_due_filters_orders_and_limits(
    repository: SqlAlchemyMissionRepository,
) -> None:
    current_time = aware_datetime()
    earlier_mission = make_mission(
        status=MissionStatus.waiting,
        scheduled_at=current_time - timedelta(minutes=2),
    )
    later_mission = make_mission(
        status=MissionStatus.waiting,
        scheduled_at=current_time - timedelta(minutes=1),
    )
    future_mission = make_mission(
        status=MissionStatus.waiting,
        scheduled_at=current_time + timedelta(minutes=1),
    )
    created_mission = make_mission(
        status=MissionStatus.created,
        scheduled_at=current_time,
    )
    unscheduled_mission = make_mission(status=MissionStatus.waiting)
    for mission in [
        later_mission,
        future_mission,
        created_mission,
        unscheduled_mission,
        earlier_mission,
    ]:
        await repository.create(mission)

    due_missions = await repository.list_due(current_time, limit=1)

    assert [mission.id for mission in due_missions] == [earlier_mission.id]


async def _list_due_rejects_invalid_arguments(
    repository: SqlAlchemyMissionRepository,
) -> None:
    with pytest.raises(InvalidRepositoryTimeError):
        await repository.list_due(datetime(2026, 8, 1, 10, 0))

    with pytest.raises(ValueError):
        await repository.list_due(aware_datetime(), limit=0)


async def _claim_due_filters_orders_limits(
    repository: SqlAlchemyMissionRepository,
) -> None:
    current_time = aware_datetime()
    earlier_mission = make_mission(
        status=MissionStatus.waiting,
        scheduled_at=current_time - timedelta(minutes=2),
    )
    earlier_mission.provider_id = "mock_train"
    later_mission = make_mission(
        status=MissionStatus.waiting,
        scheduled_at=current_time - timedelta(minutes=1),
    )
    future_mission = make_mission(
        status=MissionStatus.waiting,
        scheduled_at=current_time + timedelta(minutes=1),
    )
    created_mission = make_mission(
        status=MissionStatus.created,
        scheduled_at=current_time,
    )
    for mission in [
        later_mission,
        future_mission,
        created_mission,
        earlier_mission,
    ]:
        await repository.create(mission)

    claimed_missions = await repository.claim_due(current_time, limit=1)

    assert [mission.id for mission in claimed_missions] == [earlier_mission.id]
    assert claimed_missions[0].status is MissionStatus.processing
    assert claimed_missions[0].claimed_at == current_time
    assert claimed_missions[0].execution_attempts == 1
    assert claimed_missions[0].provider_id == "mock_train"
    loaded_future_mission = await repository.get(future_mission.id)
    loaded_created_mission = await repository.get(created_mission.id)
    assert loaded_future_mission is not None
    assert loaded_future_mission.claimed_at is None
    assert loaded_created_mission is not None
    assert loaded_created_mission.claimed_at is None


async def _claim_due_does_not_claim_twice(
    repository: SqlAlchemyMissionRepository,
) -> None:
    current_time = aware_datetime()
    mission = make_mission(
        status=MissionStatus.waiting,
        scheduled_at=current_time,
    )
    await repository.create(mission)

    first_claim = await repository.claim_due(current_time)
    second_claim = await repository.claim_due(
        current_time + timedelta(minutes=5)
    )
    loaded_mission = await repository.get(mission.id)

    assert [mission.id for mission in first_claim] == [mission.id]
    assert second_claim == []
    assert first_claim[0].claimed_at == current_time
    assert loaded_mission is not None
    assert loaded_mission.claimed_at == current_time
    assert loaded_mission.execution_attempts == 1


async def _claim_due_skips_exhausted(
    repository: SqlAlchemyMissionRepository,
) -> None:
    current_time = aware_datetime()
    available_mission = make_mission(
        status=MissionStatus.waiting,
        scheduled_at=current_time,
    )
    exhausted_mission = make_mission(
        status=MissionStatus.waiting,
        scheduled_at=current_time,
    )
    exhausted_mission.execution_attempts = 2
    exhausted_mission.max_execution_attempts = 2
    await repository.create(available_mission)
    await repository.create(exhausted_mission)

    claimed_missions = await repository.claim_due(current_time)
    loaded_exhausted_mission = await repository.get(exhausted_mission.id)

    assert [mission.id for mission in claimed_missions] == [
        available_mission.id
    ]
    assert claimed_missions[0].execution_attempts == 1
    assert loaded_exhausted_mission is not None
    assert loaded_exhausted_mission.status is MissionStatus.waiting
    assert loaded_exhausted_mission.execution_attempts == 2


def make_mission(
    best_option: ProviderOption | None = None,
    status: MissionStatus = MissionStatus.created,
    scheduled_at: datetime | None = None,
) -> Mission:
    return Mission(
        id=uuid4(),
        type=MissionType.train_trip,
        title="Moscow to Saint Petersburg",
        status=status,
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
        scheduled_at=scheduled_at,
        execution_log=[
            ExecutionEvent(
                timestamp=datetime(2026, 7, 13, 10, 0),
                type="mission_started",
                message="Mission started.",
            )
        ],
        best_option=best_option,
    )


def aware_datetime() -> datetime:
    return datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)


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

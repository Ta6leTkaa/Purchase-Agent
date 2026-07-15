from datetime import date, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

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

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_create_saves_mission(test_session: AsyncSession) -> None:
    repository = SqlAlchemyMissionRepository(test_session)
    mission = make_mission()

    created_mission = await repository.create(mission)
    loaded_mission = await repository.get(mission.id)

    assert created_mission == mission
    assert loaded_mission == mission


async def test_get_returns_created_mission(test_session: AsyncSession) -> None:
    repository = SqlAlchemyMissionRepository(test_session)
    mission = make_mission()
    await repository.create(mission)

    loaded_mission = await repository.get(mission.id)

    assert loaded_mission == mission


async def test_list_returns_multiple_missions(
    test_session: AsyncSession,
) -> None:
    repository = SqlAlchemyMissionRepository(test_session)
    first_mission = make_mission()
    second_mission = make_mission()
    await repository.create(first_mission)
    await repository.create(second_mission)

    missions = await repository.list()

    assert {mission.id for mission in missions} == {
        first_mission.id,
        second_mission.id,
    }


async def test_update_saves_new_status(test_session: AsyncSession) -> None:
    repository = SqlAlchemyMissionRepository(test_session)
    mission = make_mission()
    await repository.create(mission)
    mission.status = MissionStatus.completed

    updated_mission = await repository.update(mission)
    loaded_mission = await repository.get(mission.id)

    assert updated_mission.status is MissionStatus.completed
    assert loaded_mission is not None
    assert loaded_mission.status is MissionStatus.completed


async def test_update_saves_execution_log(test_session: AsyncSession) -> None:
    repository = SqlAlchemyMissionRepository(test_session)
    mission = make_mission()
    await repository.create(mission)
    mission.execution_log.append(make_execution_event())

    updated_mission = await repository.update(mission)
    loaded_mission = await repository.get(mission.id)

    assert updated_mission.execution_log[-1].type == "mission_completed"
    assert loaded_mission is not None
    assert loaded_mission.execution_log == mission.execution_log


async def test_update_saves_best_option(test_session: AsyncSession) -> None:
    repository = SqlAlchemyMissionRepository(test_session)
    mission = make_mission(best_option=None)
    await repository.create(mission)
    mission.best_option = make_provider_option()

    updated_mission = await repository.update(mission)
    loaded_mission = await repository.get(mission.id)

    assert updated_mission.best_option is not None
    assert updated_mission.best_option.train_number == "001A"
    assert loaded_mission is not None
    assert loaded_mission.best_option == mission.best_option


async def test_constraints_and_fallback_rules_are_restored(
    test_session: AsyncSession,
) -> None:
    repository = SqlAlchemyMissionRepository(test_session)
    mission = make_mission()
    await repository.create(mission)

    loaded_mission = await repository.get(mission.id)

    assert loaded_mission is not None
    assert loaded_mission.constraints == mission.constraints
    assert loaded_mission.fallback_rules == mission.fallback_rules


async def test_participant_ids_are_restored_as_uuids(
    test_session: AsyncSession,
) -> None:
    repository = SqlAlchemyMissionRepository(test_session)
    participant_ids = [uuid4(), uuid4(), uuid4(), uuid4()]
    mission = make_mission(participant_ids=participant_ids)
    await repository.create(mission)

    loaded_mission = await repository.get(mission.id)

    assert loaded_mission is not None
    assert loaded_mission.participant_ids == participant_ids
    assert all(
        isinstance(participant_id, UUID)
        for participant_id in loaded_mission.participant_ids
    )


async def test_clear_deletes_all_missions(test_session: AsyncSession) -> None:
    repository = SqlAlchemyMissionRepository(test_session)
    await repository.create(make_mission())
    await repository.create(make_mission())

    await repository.clear()

    assert await repository.list() == []


async def test_get_unknown_mission_returns_none(
    test_session: AsyncSession,
) -> None:
    repository = SqlAlchemyMissionRepository(test_session)

    assert await repository.get(uuid4()) is None


async def test_data_is_available_in_new_session_after_external_commit(
    test_engine: AsyncEngine,
    clean_database: None,
) -> None:
    session_maker = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    mission = make_mission()

    async with session_maker() as session:
        repository = SqlAlchemyMissionRepository(session)
        await repository.create(mission)
        await session.commit()

    async with session_maker() as session:
        repository = SqlAlchemyMissionRepository(session)
        loaded_mission = await repository.get(mission.id)

    assert loaded_mission == mission


async def test_repository_does_not_commit_without_external_commit(
    test_engine: AsyncEngine,
    clean_database: None,
) -> None:
    session_maker = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    mission = make_mission()

    async with session_maker() as session:
        repository = SqlAlchemyMissionRepository(session)
        await repository.create(mission)

    async with session_maker() as session:
        repository = SqlAlchemyMissionRepository(session)
        loaded_mission = await repository.get(mission.id)

    assert loaded_mission is None


def make_mission(
    participant_ids: list[UUID] | None = None,
    best_option: ProviderOption | None = None,
) -> Mission:
    return Mission(
        id=uuid4(),
        type=MissionType.train_trip,
        title="Moscow to Saint Petersburg",
        status=MissionStatus.created,
        participant_ids=participant_ids or [
            uuid4(),
            uuid4(),
            uuid4(),
            uuid4(),
        ],
        provider="mock_train",
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Saint Petersburg",
            travel_date=date(2026, 8, 1),
            passengers_count=4,
            must_be_same_compartment=True,
            min_lower_berths=2,
            max_total_price=30000,
            avoid_toilet=True,
        ),
        fallback_rules=FallbackRules(
            allow_adjacent_compartments=True,
            allow_any_coupe_seats=True,
            notify_only_if_no_match=False,
        ),
        execution_log=[],
        best_option=best_option,
    )


def make_execution_event() -> ExecutionEvent:
    return ExecutionEvent(
        timestamp=datetime(2026, 7, 13, 11, 0),
        type="mission_completed",
        message="Mission completed.",
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
        total_price=28400,
        seats=[
            Seat(
                carriage_number=1,
                compartment_number=1,
                seat_number=1,
                berth=SeatBerth.lower,
            ),
            Seat(
                carriage_number=1,
                compartment_number=1,
                seat_number=2,
                berth=SeatBerth.lower,
            ),
            Seat(
                carriage_number=1,
                compartment_number=1,
                seat_number=3,
                berth=SeatBerth.upper,
            ),
            Seat(
                carriage_number=1,
                compartment_number=1,
                seat_number=4,
                berth=SeatBerth.upper,
            ),
        ],
    )

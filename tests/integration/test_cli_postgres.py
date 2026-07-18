import io
import json
from datetime import date, datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.cli import process_due_command
from app.domain.identity import Identity
from app.domain.mission import (
    FallbackRules,
    Mission,
    MissionStatus,
    MissionType,
    TrainConstraints,
)
from app.repositories.sqlalchemy.identity import SqlAlchemyIdentityRepository
from app.repositories.sqlalchemy.mission import SqlAlchemyMissionRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

CURRENT_TIME = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)


async def test_cli_process_due_persists_postgres_updates(
    test_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    identities = [make_identity() for _ in range(4)]
    mission = make_mission([identity.id for identity in identities])
    await create_execution_data(test_session, identities, mission)
    session_maker = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    stdout = io.StringIO()

    exit_code = await process_due_command(
        100,
        session_maker=session_maker,
        current_time=CURRENT_TIME,
        stdout=stdout,
    )
    persisted_mission = await load_mission(test_engine, mission.id)
    output = json.loads(stdout.getvalue())

    assert exit_code == 0
    assert output["processed_count"] == 1
    assert output["succeeded_mission_ids"] == [str(mission.id)]
    assert persisted_mission is not None
    assert persisted_mission.status is MissionStatus.requires_confirmation
    assert persisted_mission.claimed_at is None
    assert persisted_mission.best_option is not None
    assert persisted_mission.best_option.train_number == "001A"
    assert "waiting_for_user_confirmation" in [
        event.type for event in persisted_mission.execution_log
    ]


async def create_execution_data(
    session: AsyncSession,
    identities: list[Identity],
    mission: Mission,
) -> None:
    identity_repository = SqlAlchemyIdentityRepository(session)
    mission_repository = SqlAlchemyMissionRepository(session)

    for identity in identities:
        await identity_repository.create(identity)
    await mission_repository.create(mission)
    await session.commit()


async def load_mission(
    engine: AsyncEngine,
    mission_id: UUID,
) -> Mission | None:
    session_maker = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_maker() as session:
        repository = SqlAlchemyMissionRepository(session)
        return await repository.get(mission_id)


def make_identity() -> Identity:
    return Identity(
        id=uuid4(),
        display_name="Ivan Petrov",
        first_name="Ivan",
        last_name="Petrov",
        birth_date=date(1990, 1, 1),
    )


def make_mission(participant_ids: list[UUID]) -> Mission:
    return Mission(
        id=uuid4(),
        type=MissionType.train_trip,
        title="Family train trip",
        status=MissionStatus.waiting,
        participant_ids=participant_ids,
        provider="mock_train",
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Saint Petersburg",
            travel_date=date(2026, 8, 1),
            passengers_count=len(participant_ids),
            must_be_same_compartment=True,
            min_lower_berths=2,
            max_total_price=30000,
            avoid_toilet=True,
        ),
        fallback_rules=FallbackRules(allow_adjacent_compartments=True),
        scheduled_at=CURRENT_TIME,
        execution_log=[],
        best_option=None,
    )

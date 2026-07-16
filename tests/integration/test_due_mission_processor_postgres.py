from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

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
from app.services.due_mission_processor import process_due_missions

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_due_mission_processor_persists_postgres_updates(
    test_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    current_time = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
    identity_repository = SqlAlchemyIdentityRepository(test_session)
    mission_repository = SqlAlchemyMissionRepository(test_session)
    identities = [make_identity() for _ in range(4)]
    due_missions = [
        make_mission(
            [identity.id for identity in identities],
            scheduled_at=current_time - timedelta(minutes=2),
        ),
        make_mission(
            [identity.id for identity in identities],
            scheduled_at=current_time - timedelta(minutes=1),
        ),
    ]

    for identity in identities:
        await identity_repository.create(identity)
    for mission in due_missions:
        await mission_repository.create(mission)
    await test_session.commit()

    result = await process_due_missions(
        mission_repository,
        identity_repository,
        current_time,
    )
    await test_session.commit()

    persisted_missions = [
        await load_mission(test_engine, mission.id)
        for mission in due_missions
    ]

    assert result.processed_count == 2
    assert result.succeeded_mission_ids == [
        mission.id for mission in due_missions
    ]
    assert result.failed_mission_ids == []
    for persisted_mission in persisted_missions:
        assert persisted_mission is not None
        assert persisted_mission.status is MissionStatus.requires_confirmation
        assert persisted_mission.best_option is not None
        assert persisted_mission.best_option.train_number == "001A"
        assert "waiting_for_user_confirmation" in [
            event.type for event in persisted_mission.execution_log
        ]


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


def make_mission(
    participant_ids: list[UUID],
    scheduled_at: datetime,
) -> Mission:
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
        scheduled_at=scheduled_at,
        execution_log=[],
        best_option=None,
    )

import asyncio
from datetime import UTC, date, datetime
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import MissionEventModel
from app.domain.mission import Mission, MissionType, TrainConstraints
from app.repositories.sqlalchemy.mission import SqlAlchemyMissionRepository
from app.repositories.sqlalchemy.mission_event import (
    SqlAlchemyMissionEventProjectionRepository,
)
from app.services.mission_event_projection import (
    MissionEventProjectionVerificationStatus,
    compare_mission_event_projection,
    mission_event_projection_matches,
    rebuild_mission_event_projection,
)


def make_mission() -> Mission:
    return Mission(
        id=uuid4(),
        type=MissionType.TRAIN_TICKET,
        title="Projection",
        participant_ids=[uuid4()],
        provider="mock_train",
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Saint Petersburg",
            travel_date=date(2026, 8, 1),
            passengers_count=1,
        ),
    )


def test_sqlalchemy_repository_projects_new_mission_events() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_maker = async_sessionmaker(engine, expire_on_commit=False)
        mission = make_mission()

        async with session_maker() as session:
            repository = SqlAlchemyMissionRepository(session)
            await repository.create(mission)
            mission.record_event(
                timestamp=datetime(2026, 7, 29, 12, 0, tzinfo=UTC),
                event_type="mission_scheduled",
                message="Mission scheduled.",
            )
            await repository.update(mission)
            await session.commit()

        async with session_maker() as session:
            events = await SqlAlchemyMissionEventProjectionRepository(
                session
            ).list_after(mission.id, 0, 10)
            models = (
                await session.execute(
                    select(MissionEventModel).where(
                        MissionEventModel.mission_id == mission.id
                    )
                )
            ).scalars().all()

        assert [event.sequence for event in events] == [1]
        assert events[0].type == "mission_scheduled"
        assert len(models) == 1
        await engine.dispose()

    asyncio.run(scenario())


def test_projection_verification_reports_differences() -> None:
    mission = make_mission()
    canonical = mission.record_event(
        timestamp=datetime(2026, 7, 29, 12, 0, tzinfo=UTC),
        event_type="mission_scheduled",
        message="Mission scheduled.",
    )
    changed = canonical.model_copy(update={"message": "Unexpected text."})
    unexpected = canonical.model_copy(
        update={"sequence": 2, "event_id": uuid4()}
    )

    verification = compare_mission_event_projection(
        mission_id=mission.id,
        canonical_events=[canonical],
        projected_events=[changed, unexpected],
    )

    assert verification.status is MissionEventProjectionVerificationStatus.INCONSISTENT
    assert verification.canonical_event_count == 1
    assert verification.projection_event_count == 2
    assert verification.missing_projection_sequences == ()
    assert verification.unexpected_projection_sequences == (2,)
    assert [mismatch.sequence for mismatch in verification.mismatches] == [1]


def test_rebuild_restores_mission_event_projection_from_canonical_log() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_maker = async_sessionmaker(engine, expire_on_commit=False)
        mission = make_mission()

        async with session_maker() as session:
            repository = SqlAlchemyMissionRepository(session)
            await repository.create(mission)
            mission.record_event(
                timestamp=datetime(2026, 7, 29, 12, 0, tzinfo=UTC),
                event_type="mission_scheduled",
                message="Mission scheduled.",
            )
            await repository.update(mission)
            await session.commit()
            await session.execute(delete(MissionEventModel))
            result = await rebuild_mission_event_projection(session)
            await session.commit()

        async with session_maker() as session:
            projected = await SqlAlchemyMissionEventProjectionRepository(
                session
            ).list_all(mission.id)

        assert result.processed_missions == 1
        assert result.inserted_events == 1
        assert mission_event_projection_matches(mission.execution_log, projected)
        await engine.dispose()

    asyncio.run(scenario())

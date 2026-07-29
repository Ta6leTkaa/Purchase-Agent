from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.mission_event import MissionEventModel
from app.domain.mission import Mission, MissionType, TrainConstraints
from app.repositories.sqlalchemy.mission import SqlAlchemyMissionRepository
from app.repositories.sqlalchemy.mission_event import (
    SqlAlchemyMissionEventProjectionRepository,
)
from app.services.mission_event_projection import (
    MissionEventProjectionVerificationStatus,
    RebuildMissionEventProjection,
    VerifyMissionEventProjection,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_postgres_event_projection_can_be_verified_and_rebuilt(
    test_session: AsyncSession,
) -> None:
    mission = Mission(
        id=uuid4(),
        type=MissionType.TRAIN_TICKET,
        title="Projection maintenance",
        participant_ids=[uuid4()],
        provider="mock_train",
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Saint Petersburg",
            travel_date=date(2026, 8, 1),
            passengers_count=1,
        ),
    )
    repository = SqlAlchemyMissionRepository(test_session)
    await repository.create(mission)
    mission.record_event(
        timestamp=datetime(2026, 7, 29, 12, 0, tzinfo=UTC),
        event_type="mission_scheduled",
        message="Mission scheduled.",
    )
    await repository.update(mission)
    await test_session.commit()

    reader = SqlAlchemyMissionEventProjectionRepository(test_session)
    verifier = VerifyMissionEventProjection(repository, reader)
    consistent = await verifier.execute(mission.id)

    assert consistent.status is MissionEventProjectionVerificationStatus.CONSISTENT
    assert consistent.canonical_event_count == 1

    await test_session.execute(
        delete(MissionEventModel).where(MissionEventModel.mission_id == mission.id)
    )
    await test_session.flush()
    inconsistent = await verifier.execute(mission.id)

    assert inconsistent.status is MissionEventProjectionVerificationStatus.INCONSISTENT
    assert inconsistent.missing_projection_sequences == (1,)

    result = await RebuildMissionEventProjection().execute(test_session)
    await test_session.commit()
    rebuilt = await verifier.execute(mission.id)

    assert result.processed_missions == 1
    assert result.inserted_events == 1
    assert rebuilt.status is MissionEventProjectionVerificationStatus.CONSISTENT

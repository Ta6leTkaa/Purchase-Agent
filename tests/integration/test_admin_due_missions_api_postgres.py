from collections.abc import AsyncIterator
from datetime import date, datetime, timezone
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import settings
from app.dependencies import (
    get_current_time,
    get_identity_repository,
    get_mission_repository,
)
from app.domain.identity import Identity
from app.domain.mission import (
    FallbackRules,
    Mission,
    MissionStatus,
    MissionType,
    TrainConstraints,
)
from app.main import app
from app.repositories.sqlalchemy.identity import SqlAlchemyIdentityRepository
from app.repositories.sqlalchemy.mission import SqlAlchemyMissionRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

CURRENT_TIME = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
ADMIN_HEADERS = {"X-Admin-API-Key": "test-admin-key"}


@pytest.fixture()
async def admin_client(
    test_session: AsyncSession,
) -> AsyncIterator[AsyncClient]:
    original_admin_api_key = settings.admin_api_key
    settings.admin_api_key = SecretStr("test-admin-key")

    async def override_identity_repository() -> AsyncIterator[
        SqlAlchemyIdentityRepository
    ]:
        try:
            yield SqlAlchemyIdentityRepository(test_session)
            await test_session.commit()
        except Exception:
            await test_session.rollback()
            raise

    async def override_mission_repository() -> AsyncIterator[
        SqlAlchemyMissionRepository
    ]:
        try:
            yield SqlAlchemyMissionRepository(test_session)
            await test_session.commit()
        except Exception:
            await test_session.rollback()
            raise

    app.dependency_overrides[get_identity_repository] = (
        override_identity_repository
    )
    app.dependency_overrides[get_mission_repository] = (
        override_mission_repository
    )
    app.dependency_overrides[get_current_time] = lambda: CURRENT_TIME
    transport = ASGITransport(app=app)

    try:
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        settings.admin_api_key = original_admin_api_key


async def test_admin_process_due_persists_postgres_updates(
    admin_client: AsyncClient,
    test_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    identities = [make_identity() for _ in range(4)]
    mission = make_mission([identity.id for identity in identities])
    await create_execution_data(test_session, identities, mission)

    response = await admin_client.post(
        "/admin/missions/process-due",
        json={"limit": 100},
        headers=ADMIN_HEADERS,
    )
    persisted_mission = await load_mission(test_engine, mission.id)

    assert response.status_code == 200
    assert response.json()["processed_count"] == 1
    assert response.json()["succeeded_mission_ids"] == [str(mission.id)]
    assert response.json()["failed_mission_ids"] == []
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

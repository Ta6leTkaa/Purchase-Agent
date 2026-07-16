from collections.abc import AsyncIterator
from datetime import date
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.dependencies import get_identity_repository, get_mission_repository
from app.domain.identity import (
    Document,
    DocumentType,
    Identity,
    Preferences,
    TrainPreferences,
)
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


@pytest.fixture()
async def mission_execution_client(
    test_session: AsyncSession,
) -> AsyncIterator[tuple[AsyncClient, list[str]]]:
    transaction_events: list[str] = []

    async def override_identity_repository() -> AsyncIterator[
        SqlAlchemyIdentityRepository
    ]:
        try:
            yield SqlAlchemyIdentityRepository(test_session)
            await test_session.commit()
            transaction_events.append("identity_commit")
        except Exception:
            await test_session.rollback()
            transaction_events.append("identity_rollback")
            raise

    async def override_mission_repository() -> AsyncIterator[
        SqlAlchemyMissionRepository
    ]:
        try:
            yield SqlAlchemyMissionRepository(test_session)
            await test_session.commit()
            transaction_events.append("mission_commit")
        except Exception:
            await test_session.rollback()
            transaction_events.append("mission_rollback")
            raise

    app.dependency_overrides[get_identity_repository] = (
        override_identity_repository
    )
    app.dependency_overrides[get_mission_repository] = (
        override_mission_repository
    )
    transport = ASGITransport(app=app)

    try:
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            yield client, transaction_events
    finally:
        app.dependency_overrides.clear()


async def test_mission_execution_flow_persists_result_to_postgres(
    mission_execution_client: tuple[AsyncClient, list[str]],
    test_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    client, transaction_events = mission_execution_client
    identities = [make_identity() for _ in range(4)]
    mission = make_mission(
        participant_ids=[identity.id for identity in identities],
    )
    await create_execution_data(test_session, identities, mission)

    response = await client.post(f"/missions/{mission.id}/run")

    assert response.status_code == 200
    response_body = response.json()
    assert response_body["status"] == "requires_confirmation"
    assert response_body["best_option"] is not None
    assert response_body["best_option"]["train_number"] == "001A"
    assert_event_types_contain(
        response_body["execution_log"],
        [
            "mission_started",
            "search_started",
            "options_found",
            "best_option_selected",
            "reservation_started",
            "waiting_for_user_confirmation",
        ],
    )
    assert "mission_commit" in transaction_events
    assert "identity_commit" in transaction_events

    persisted_mission = await load_mission(test_engine, mission.id)

    assert persisted_mission is not None
    assert persisted_mission.status is MissionStatus.requires_confirmation
    assert persisted_mission.best_option is not None
    assert persisted_mission.best_option.train_number == "001A"
    assert_event_types_contain(
        [
            event.model_dump(mode="json")
            for event in persisted_mission.execution_log
        ],
        [
            "mission_started",
            "search_started",
            "options_found",
            "best_option_selected",
            "reservation_started",
            "waiting_for_user_confirmation",
        ],
    )


async def test_mission_execution_failure_persists_missing_participant(
    mission_execution_client: tuple[AsyncClient, list[str]],
    test_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    client, transaction_events = mission_execution_client
    identities = [make_identity() for _ in range(3)]
    participant_ids = [identity.id for identity in identities]
    participant_ids.append(uuid4())
    mission = make_mission(participant_ids=participant_ids)
    await create_execution_data(test_session, identities, mission)

    response = await client.post(f"/missions/{mission.id}/run")

    assert response.status_code == 200
    response_body = response.json()
    assert response_body["status"] == "failed"
    assert_event_types_contain(
        response_body["execution_log"],
        ["participant_missing"],
    )
    assert "mission_commit" in transaction_events

    persisted_mission = await load_mission(test_engine, mission.id)

    assert persisted_mission is not None
    assert persisted_mission.status is MissionStatus.failed
    assert [event.type for event in persisted_mission.execution_log] == [
        "participant_missing"
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


def assert_event_types_contain(
    execution_log: list[dict[str, object]],
    expected_types: list[str],
) -> None:
    event_types = [event["type"] for event in execution_log]

    for expected_type in expected_types:
        assert expected_type in event_types


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


def make_mission(participant_ids: list[UUID]) -> Mission:
    return Mission(
        id=uuid4(),
        type=MissionType.train_trip,
        title="Family train trip",
        status=MissionStatus.created,
        participant_ids=participant_ids,
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
        ),
        execution_log=[],
        best_option=None,
    )

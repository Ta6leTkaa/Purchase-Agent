from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.dependencies import get_identity_repository, get_mission_repository
from app.main import app
from app.repositories.sqlalchemy.identity import SqlAlchemyIdentityRepository
from app.repositories.sqlalchemy.mission import SqlAlchemyMissionRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture()
async def mission_api_client(
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


async def test_mission_api_persists_mission_to_postgres(
    mission_api_client: tuple[AsyncClient, list[str]],
    test_engine: AsyncEngine,
) -> None:
    client, transaction_events = mission_api_client
    participant_ids = [
        await create_identity(client)
        for _ in range(4)
    ]
    payload = make_mission_payload(participant_ids)

    create_response = await client.post("/missions", json=payload)
    mission_id = create_response.json()["id"]

    assert create_response.status_code in {200, 201}
    assert mission_id is not None
    assert create_response.json()["status"] == "created"
    assert "mission_commit" in transaction_events

    get_response = await client.get(f"/missions/{mission_id}")
    list_response = await client.get("/missions")

    assert get_response.status_code == 200
    assert get_response.json()["participant_ids"] == participant_ids
    assert get_response.json()["constraints"] == payload["constraints"]
    assert get_response.json()["fallback_rules"] == payload["fallback_rules"]
    assert get_response.json()["execution_log"] == []
    assert get_response.json()["best_option"] is None
    assert list_response.status_code == 200
    assert [mission["id"] for mission in list_response.json()] == [mission_id]

    session_maker = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_maker() as session:
        repository = SqlAlchemyMissionRepository(session)
        persisted_mission = await repository.get(UUID(mission_id))

    assert persisted_mission is not None
    assert str(persisted_mission.id) == mission_id
    assert [
        str(participant_id)
        for participant_id in persisted_mission.participant_ids
    ] == participant_ids
    assert persisted_mission.constraints.passengers_count == 4
    assert persisted_mission.fallback_rules.notify_only_if_no_match is True
    assert persisted_mission.execution_log == []
    assert persisted_mission.best_option is None


async def test_mission_api_persists_scheduled_at_to_postgres(
    mission_api_client: tuple[AsyncClient, list[str]],
    test_engine: AsyncEngine,
) -> None:
    client, _transaction_events = mission_api_client
    scheduled_at = datetime.now(timezone.utc) + timedelta(days=1)
    participant_ids = [
        await create_identity(client)
        for _ in range(4)
    ]
    payload = {
        **make_mission_payload(participant_ids),
        "scheduled_at": scheduled_at.isoformat(),
    }

    create_response = await client.post("/missions", json=payload)
    mission_id = create_response.json()["id"]

    assert create_response.status_code in {200, 201}
    assert create_response.json()["status"] == "waiting"

    session_maker = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_maker() as session:
        repository = SqlAlchemyMissionRepository(session)
        persisted_mission = await repository.get(UUID(mission_id))

    assert persisted_mission is not None
    assert persisted_mission.status.value == "waiting"
    assert persisted_mission.scheduled_at == scheduled_at


async def test_mission_api_rejects_unknown_participants_without_persisting(
    mission_api_client: tuple[AsyncClient, list[str]],
    test_engine: AsyncEngine,
) -> None:
    client, _transaction_events = mission_api_client
    known_participant_ids = [
        await create_identity(client)
        for _ in range(2)
    ]
    unknown_participant_id = str(uuid4())
    payload = make_mission_payload(
        [*known_participant_ids, unknown_participant_id]
    )
    payload["constraints"] = {
        **payload["constraints"],
        "passengers_count": 3,
    }

    response = await client.post("/missions", json=payload)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unknown_participants"
    assert response.json()["detail"]["participant_ids"] == [
        unknown_participant_id
    ]

    session_maker = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_maker() as session:
        repository = SqlAlchemyMissionRepository(session)
        missions = await repository.list()

    assert missions == []


async def test_mission_api_returns_404_for_unknown_mission(
    mission_api_client: tuple[AsyncClient, list[str]],
) -> None:
    client, transaction_events = mission_api_client

    response = await client.get(f"/missions/{uuid4()}")

    assert response.status_code == 404
    assert "mission_rollback" in transaction_events


async def test_invalid_mission_is_not_persisted(
    mission_api_client: tuple[AsyncClient, list[str]],
    test_engine: AsyncEngine,
) -> None:
    client, _transaction_events = mission_api_client
    participant_ids = [str(uuid4()), str(uuid4()), str(uuid4()), str(uuid4())]
    payload = make_mission_payload(participant_ids)
    payload["constraints"] = {
        **payload["constraints"],
        "passengers_count": 0,
    }

    response = await client.post("/missions", json=payload)

    assert response.status_code == 422

    session_maker = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_maker() as session:
        repository = SqlAlchemyMissionRepository(session)
        missions = await repository.list()

    assert missions == []


def make_mission_payload(
    participant_ids: list[str],
) -> dict[str, Any]:
    return {
        "type": "train_trip",
        "title": "Family train trip",
        "participant_ids": participant_ids,
        "provider": "mock_train",
        "constraints": {
            "from_city": "Moscow",
            "to_city": "Saint Petersburg",
            "travel_date": "2026-08-01",
            "passengers_count": 4,
            "must_be_same_compartment": True,
            "min_lower_berths": 2,
            "max_total_price": 30000,
            "avoid_toilet": True,
        },
        "fallback_rules": {
            "allow_adjacent_compartments": True,
            "allow_any_coupe_seats": False,
            "notify_only_if_no_match": True,
        },
    }


async def create_identity(client: AsyncClient) -> str:
    response = await client.post(
        "/identities",
        json={
            "display_name": "Ivan Petrov",
            "first_name": "Ivan",
            "last_name": "Petrov",
            "birth_date": "1990-01-01",
        },
    )
    return str(response.json()["id"])

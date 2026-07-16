from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.dependencies import get_mission_repository
from app.main import app
from app.repositories.sqlalchemy.mission import SqlAlchemyMissionRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture()
async def mission_api_client(
    test_session: AsyncSession,
) -> AsyncIterator[tuple[AsyncClient, list[str]]]:
    transaction_events: list[str] = []

    async def override_mission_repository() -> AsyncIterator[
        SqlAlchemyMissionRepository
    ]:
        try:
            yield SqlAlchemyMissionRepository(test_session)
            await test_session.commit()
            transaction_events.append("commit")
        except Exception:
            await test_session.rollback()
            transaction_events.append("rollback")
            raise

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
    mission_id = str(uuid4())
    participant_ids = [str(uuid4()), str(uuid4()), str(uuid4()), str(uuid4())]
    payload = make_mission_payload(mission_id, participant_ids)

    create_response = await client.post("/missions", json=payload)

    assert create_response.status_code in {200, 201}
    assert create_response.json()["id"] == mission_id
    assert create_response.json()["status"] == "created"
    assert "commit" in transaction_events

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


async def test_mission_api_returns_404_for_unknown_mission(
    mission_api_client: tuple[AsyncClient, list[str]],
) -> None:
    client, transaction_events = mission_api_client

    response = await client.get(f"/missions/{uuid4()}")

    assert response.status_code == 404
    assert "rollback" in transaction_events


async def test_invalid_mission_is_not_persisted(
    mission_api_client: tuple[AsyncClient, list[str]],
    test_engine: AsyncEngine,
) -> None:
    client, _transaction_events = mission_api_client
    mission_id = str(uuid4())
    participant_ids = [str(uuid4()), str(uuid4()), str(uuid4()), str(uuid4())]
    payload = make_mission_payload(mission_id, participant_ids)
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
        persisted_mission = await repository.get(UUID(mission_id))

    assert persisted_mission is None


def make_mission_payload(
    mission_id: str,
    participant_ids: list[str],
) -> dict[str, Any]:
    return {
        "id": mission_id,
        "type": "train_trip",
        "title": "Family train trip",
        "status": "created",
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

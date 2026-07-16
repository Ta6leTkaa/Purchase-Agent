from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.dependencies import get_identity_repository
from app.main import app
from app.repositories.sqlalchemy.identity import SqlAlchemyIdentityRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture()
async def identity_api_client(
    test_session: AsyncSession,
) -> AsyncIterator[tuple[AsyncClient, list[str]]]:
    transaction_events: list[str] = []

    async def override_identity_repository() -> AsyncIterator[
        SqlAlchemyIdentityRepository
    ]:
        try:
            yield SqlAlchemyIdentityRepository(test_session)
            await test_session.commit()
            transaction_events.append("commit")
        except Exception:
            await test_session.rollback()
            transaction_events.append("rollback")
            raise

    app.dependency_overrides[get_identity_repository] = (
        override_identity_repository
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


async def test_identity_api_persists_identity_to_postgres(
    identity_api_client: tuple[AsyncClient, list[str]],
    test_engine: AsyncEngine,
) -> None:
    client, transaction_events = identity_api_client
    payload = make_identity_payload()

    create_response = await client.post("/identities", json=payload)
    created_identity = create_response.json()
    identity_id = created_identity["id"]
    document_id = created_identity["documents"][0]["id"]

    assert create_response.status_code in {200, 201}
    assert identity_id is not None
    assert "commit" in transaction_events

    get_response = await client.get(f"/identities/{identity_id}")
    list_response = await client.get("/identities")

    assert get_response.status_code == 200
    assert get_response.json()["documents"][0]["id"] == document_id
    assert get_response.json()["preferences"]["train"]["avoid_toilet"] is True
    assert list_response.status_code == 200
    assert [identity["id"] for identity in list_response.json()] == [
        identity_id
    ]

    session_maker = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_maker() as session:
        repository = SqlAlchemyIdentityRepository(session)
        persisted_identity = await repository.get(UUID(identity_id))

    assert persisted_identity is not None
    assert str(persisted_identity.id) == identity_id
    assert persisted_identity.documents[0].number == "1234567890"
    assert persisted_identity.preferences.train.prefer_same_compartment is True


async def test_identity_api_returns_404_for_unknown_identity(
    identity_api_client: tuple[AsyncClient, list[str]],
) -> None:
    client, transaction_events = identity_api_client

    response = await client.get(f"/identities/{uuid4()}")

    assert response.status_code == 404
    assert "rollback" in transaction_events


def make_identity_payload() -> dict[str, object]:
    return {
        "display_name": "Ivan Petrov",
        "first_name": "Ivan",
        "last_name": "Petrov",
        "birth_date": "1990-01-01",
        "documents": [
            {
                "type": "internal_passport",
                "number": "1234567890",
                "expires_at": "2030-01-01",
            }
        ],
        "preferences": {
            "train": {
                "prefers_lower_berth": True,
                "avoid_toilet": True,
                "prefer_same_compartment": True,
            }
        },
    }

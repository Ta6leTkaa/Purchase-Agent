import asyncio
from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.dependencies import identity_repository
from app.main import app


@pytest.fixture(autouse=True)
def clear_repository() -> Iterator[None]:
    asyncio.run(identity_repository.clear())
    yield
    asyncio.run(identity_repository.clear())


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
            }
        ],
    }


def test_post_identities_creates_identity() -> None:
    client = TestClient(app)
    payload = make_identity_payload()

    response = client.post("/identities", json=payload)

    assert response.status_code == 200
    assert response.json()["id"] is not None
    assert response.json()["documents"][0]["number"] == "1234567890"


def test_post_identities_without_id_generates_uuid() -> None:
    client = TestClient(app)
    payload = make_identity_payload()

    response = client.post("/identities", json=payload)

    assert response.status_code == 200
    assert UUID(response.json()["id"])


def test_post_identities_with_id_returns_422() -> None:
    client = TestClient(app)
    payload = {
        **make_identity_payload(),
        "id": str(uuid4()),
    }

    response = client.post("/identities", json=payload)

    assert response.status_code == 422


def test_get_identities_returns_created_identity() -> None:
    client = TestClient(app)
    payload = make_identity_payload()
    create_response = client.post("/identities", json=payload)

    response = client.get("/identities")

    assert response.status_code == 200
    assert response.json()[0]["id"] == create_response.json()["id"]


def test_get_identity_by_id_returns_created_identity() -> None:
    client = TestClient(app)
    payload = make_identity_payload()
    create_response = client.post("/identities", json=payload)
    identity_id = create_response.json()["id"]

    response = client.get(f"/identities/{identity_id}")

    assert response.status_code == 200
    assert response.json()["id"] == identity_id


def test_get_unknown_identity_returns_404() -> None:
    client = TestClient(app)

    response = client.get(f"/identities/{uuid4()}")

    assert response.status_code == 404

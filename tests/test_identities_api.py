from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.dependencies import identity_repository
from app.main import app


@pytest.fixture(autouse=True)
def clear_repository() -> Iterator[None]:
    identity_repository.clear()
    yield
    identity_repository.clear()


def make_identity_payload() -> dict[str, object]:
    return {
        "id": str(uuid4()),
        "display_name": "Ivan Petrov",
        "first_name": "Ivan",
        "last_name": "Petrov",
        "birth_date": "1990-01-01",
        "documents": [
            {
                "id": str(uuid4()),
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
    assert response.json()["id"] == payload["id"]
    assert response.json()["documents"][0]["number"] == "1234567890"


def test_get_identities_returns_created_identity() -> None:
    client = TestClient(app)
    payload = make_identity_payload()
    client.post("/identities", json=payload)

    response = client.get("/identities")

    assert response.status_code == 200
    assert response.json()[0]["id"] == payload["id"]


def test_get_identity_by_id_returns_created_identity() -> None:
    client = TestClient(app)
    payload = make_identity_payload()
    client.post("/identities", json=payload)

    response = client.get(f"/identities/{payload['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == payload["id"]


def test_get_unknown_identity_returns_404() -> None:
    client = TestClient(app)

    response = client.get(f"/identities/{uuid4()}")

    assert response.status_code == 404

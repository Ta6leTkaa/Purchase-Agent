from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.storage.memory import store


@pytest.fixture(autouse=True)
def clear_store() -> Iterator[None]:
    store.clear()
    yield
    store.clear()


def make_mission_payload(
    *,
    participant_ids: list[str] | None = None,
    passengers_count: int = 1,
) -> dict[str, object]:
    if participant_ids is None:
        participant_ids = [str(uuid4())]

    return {
        "id": str(uuid4()),
        "type": "train_trip",
        "title": "Moscow to Saint Petersburg",
        "participant_ids": participant_ids,
        "provider": "rzd",
        "constraints": {
            "from_city": "Moscow",
            "to_city": "Saint Petersburg",
            "travel_date": "2026-08-01",
            "passengers_count": passengers_count,
        },
    }


def test_post_missions_creates_mission() -> None:
    client = TestClient(app)
    payload = make_mission_payload()

    response = client.post("/missions", json=payload)

    assert response.status_code == 200
    assert response.json()["id"] == payload["id"]
    assert response.json()["status"] == "created"


def test_get_missions_returns_created_mission() -> None:
    client = TestClient(app)
    payload = make_mission_payload()
    client.post("/missions", json=payload)

    response = client.get("/missions")

    assert response.status_code == 200
    assert response.json()[0]["id"] == payload["id"]


def test_get_mission_by_id_returns_created_mission() -> None:
    client = TestClient(app)
    payload = make_mission_payload()
    client.post("/missions", json=payload)

    response = client.get(f"/missions/{payload['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == payload["id"]


def test_get_unknown_mission_returns_404() -> None:
    client = TestClient(app)

    response = client.get(f"/missions/{uuid4()}")

    assert response.status_code == 404


def test_post_missions_with_empty_participant_ids_returns_422() -> None:
    client = TestClient(app)
    payload = make_mission_payload(participant_ids=[])

    response = client.post("/missions", json=payload)

    assert response.status_code == 422


def test_post_missions_with_zero_passengers_count_returns_422() -> None:
    client = TestClient(app)
    payload = make_mission_payload(passengers_count=0)

    response = client.post("/missions", json=payload)

    assert response.status_code == 422

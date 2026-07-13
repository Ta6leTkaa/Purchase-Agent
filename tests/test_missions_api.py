from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.dependencies import identity_repository, mission_repository
from app.main import app


@pytest.fixture(autouse=True)
def clear_repositories() -> Iterator[None]:
    identity_repository.clear()
    mission_repository.clear()
    yield
    identity_repository.clear()
    mission_repository.clear()


def make_mission_payload(
    *,
    participant_ids: list[str] | None = None,
    passengers_count: int = 1,
    provider: str = "rzd",
) -> dict[str, object]:
    if participant_ids is None:
        participant_ids = [str(uuid4())]

    return {
        "id": str(uuid4()),
        "type": "train_trip",
        "title": "Moscow to Saint Petersburg",
        "participant_ids": participant_ids,
        "provider": provider,
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


def make_identity_payload() -> dict[str, object]:
    return {
        "id": str(uuid4()),
        "display_name": "Ivan Petrov",
        "first_name": "Ivan",
        "last_name": "Petrov",
        "birth_date": "1990-01-01",
        "documents": [],
    }


def test_post_mission_run_returns_requires_confirmation() -> None:
    client = TestClient(app)
    identity_payloads = [make_identity_payload() for _ in range(4)]
    for identity_payload in identity_payloads:
        client.post("/identities", json=identity_payload)
    mission_payload = make_mission_payload(
        participant_ids=[
            str(identity_payload["id"])
            for identity_payload in identity_payloads
        ],
        passengers_count=4,
        provider="mock_train",
    )
    mission_payload["constraints"] = {
        "from_city": "Moscow",
        "to_city": "Saint Petersburg",
        "travel_date": "2026-08-01",
        "passengers_count": 4,
        "must_be_same_compartment": True,
        "min_lower_berths": 2,
        "max_total_price": 30000,
        "avoid_toilet": True,
    }
    client.post("/missions", json=mission_payload)

    response = client.post(f"/missions/{mission_payload['id']}/run")

    assert response.status_code == 200
    assert response.json()["status"] == "requires_confirmation"
    assert response.json()["best_option"]["train_number"] == "001A"


def test_post_unknown_mission_run_returns_404() -> None:
    client = TestClient(app)

    response = client.post(f"/missions/{uuid4()}/run")

    assert response.status_code == 404

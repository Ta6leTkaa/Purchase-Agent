import asyncio
from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.dependencies import identity_repository, mission_repository
from app.main import app


@pytest.fixture(autouse=True)
def clear_repositories() -> Iterator[None]:
    asyncio.run(identity_repository.clear())
    asyncio.run(mission_repository.clear())
    yield
    asyncio.run(identity_repository.clear())
    asyncio.run(mission_repository.clear())


def make_mission_payload(
    *,
    participant_ids: list[str] | None = None,
    passengers_count: int = 1,
    provider: str = "rzd",
) -> dict[str, object]:
    if participant_ids is None:
        participant_ids = [str(uuid4())]

    return {
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


def create_identity(client: TestClient) -> str:
    response = client.post("/identities", json=make_identity_payload())
    return str(response.json()["id"])


def make_existing_participant_ids(
    client: TestClient,
    count: int = 1,
) -> list[str]:
    return [create_identity(client) for _ in range(count)]


def test_post_missions_creates_mission() -> None:
    client = TestClient(app)
    payload = make_mission_payload(
        participant_ids=make_existing_participant_ids(client)
    )

    response = client.post("/missions", json=payload)

    assert response.status_code == 200
    assert response.json()["id"] is not None
    assert response.json()["status"] == "created"


def test_post_missions_without_id_generates_uuid() -> None:
    client = TestClient(app)
    payload = make_mission_payload(
        participant_ids=make_existing_participant_ids(client)
    )

    response = client.post("/missions", json=payload)

    assert response.status_code == 200
    assert UUID(response.json()["id"])


def test_post_missions_initializes_internal_fields() -> None:
    client = TestClient(app)
    payload = make_mission_payload(
        participant_ids=make_existing_participant_ids(client)
    )

    response = client.post("/missions", json=payload)

    assert response.status_code == 200
    assert response.json()["status"] == "created"
    assert response.json()["execution_log"] == []
    assert response.json()["best_option"] is None


def test_get_missions_returns_created_mission() -> None:
    client = TestClient(app)
    payload = make_mission_payload(
        participant_ids=make_existing_participant_ids(client)
    )
    create_response = client.post("/missions", json=payload)

    response = client.get("/missions")

    assert response.status_code == 200
    assert response.json()[0]["id"] == create_response.json()["id"]


def test_get_mission_by_id_returns_created_mission() -> None:
    client = TestClient(app)
    payload = make_mission_payload(
        participant_ids=make_existing_participant_ids(client)
    )
    create_response = client.post("/missions", json=payload)
    mission_id = create_response.json()["id"]

    response = client.get(f"/missions/{mission_id}")

    assert response.status_code == 200
    assert response.json()["id"] == mission_id


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
    payload = make_mission_payload(
        participant_ids=make_existing_participant_ids(client),
        passengers_count=0,
    )

    response = client.post("/missions", json=payload)

    assert response.status_code == 422


def test_post_missions_with_existing_participants_creates_mission() -> None:
    client = TestClient(app)
    participant_ids = make_existing_participant_ids(client, count=2)
    payload = make_mission_payload(
        participant_ids=participant_ids,
        passengers_count=2,
    )

    response = client.post("/missions", json=payload)

    assert response.status_code == 200
    assert response.json()["participant_ids"] == participant_ids


def test_post_missions_with_unknown_participant_returns_422() -> None:
    client = TestClient(app)
    unknown_participant_id = str(uuid4())
    payload = make_mission_payload(participant_ids=[unknown_participant_id])

    response = client.post("/missions", json=payload)

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "unknown_participants",
        "message": "One or more participants do not exist",
        "participant_ids": [unknown_participant_id],
    }
    assert asyncio.run(mission_repository.list()) == []


def test_post_missions_with_multiple_unknown_participants_returns_all() -> None:
    client = TestClient(app)
    participant_ids = [str(uuid4()), str(uuid4())]
    payload = make_mission_payload(
        participant_ids=participant_ids,
        passengers_count=2,
    )

    response = client.post("/missions", json=payload)

    assert response.status_code == 422
    assert response.json()["detail"]["participant_ids"] == participant_ids
    assert asyncio.run(mission_repository.list()) == []


def test_post_missions_with_duplicate_participant_ids_returns_422() -> None:
    client = TestClient(app)
    participant_id = create_identity(client)
    payload = make_mission_payload(
        participant_ids=[participant_id, participant_id],
        passengers_count=2,
    )

    response = client.post("/missions", json=payload)

    assert response.status_code == 422
    assert asyncio.run(mission_repository.list()) == []


def test_post_missions_with_passenger_count_mismatch_returns_422() -> None:
    client = TestClient(app)
    participant_ids = make_existing_participant_ids(client, count=2)
    payload = make_mission_payload(
        participant_ids=participant_ids,
        passengers_count=1,
    )

    response = client.post("/missions", json=payload)

    assert response.status_code == 422
    assert asyncio.run(mission_repository.list()) == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", str(uuid4())),
        ("status", "completed"),
        ("execution_log", []),
        ("best_option", None),
    ],
)
def test_post_missions_with_internal_fields_returns_422(
    field: str,
    value: object,
) -> None:
    client = TestClient(app)
    payload = {
        **make_mission_payload(),
        field: value,
    }

    response = client.post("/missions", json=payload)

    assert response.status_code == 422


def make_identity_payload() -> dict[str, object]:
    return {
        "display_name": "Ivan Petrov",
        "first_name": "Ivan",
        "last_name": "Petrov",
        "birth_date": "1990-01-01",
        "documents": [],
    }


def test_post_mission_run_returns_requires_confirmation() -> None:
    client = TestClient(app)
    identity_payloads = [make_identity_payload() for _ in range(4)]
    identity_ids: list[str] = []
    for identity_payload in identity_payloads:
        response = client.post("/identities", json=identity_payload)
        identity_ids.append(response.json()["id"])
    mission_payload = make_mission_payload(
        participant_ids=identity_ids,
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
    create_response = client.post("/missions", json=mission_payload)
    mission_id = create_response.json()["id"]

    response = client.post(f"/missions/{mission_id}/run")

    assert response.status_code == 200
    assert response.json()["status"] == "requires_confirmation"
    assert response.json()["best_option"]["train_number"] == "001A"


def test_post_mission_run_twice_returns_409() -> None:
    client = TestClient(app)
    mission_id = create_requires_confirmation_mission(client)

    response = client.post(f"/missions/{mission_id}/run")

    assert response.status_code == 409
    assert "requires_confirmation" in response.json()["detail"]


def test_post_unknown_mission_run_returns_404() -> None:
    client = TestClient(app)

    response = client.post(f"/missions/{uuid4()}/run")

    assert response.status_code == 404


def create_requires_confirmation_mission(client: TestClient) -> str:
    identity_payloads = [make_identity_payload() for _ in range(4)]
    identity_ids: list[str] = []
    for identity_payload in identity_payloads:
        response = client.post("/identities", json=identity_payload)
        identity_ids.append(response.json()["id"])
    mission_payload = make_mission_payload(
        participant_ids=identity_ids,
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
    create_response = client.post("/missions", json=mission_payload)
    mission_id = create_response.json()["id"]
    client.post(f"/missions/{mission_id}/run")
    return str(mission_id)


def test_post_mission_confirm_returns_completed() -> None:
    client = TestClient(app)
    mission_id = create_requires_confirmation_mission(client)

    response = client.post(f"/missions/{mission_id}/confirm")
    event_types = [event["type"] for event in response.json()["execution_log"]]

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert "mission_confirmed" in event_types
    assert "mission_completed" in event_types


def test_post_mission_confirm_before_run_returns_409() -> None:
    client = TestClient(app)
    payload = make_mission_payload(
        participant_ids=make_existing_participant_ids(client)
    )
    create_response = client.post("/missions", json=payload)
    mission_id = create_response.json()["id"]

    response = client.post(f"/missions/{mission_id}/confirm")

    assert response.status_code == 409


def test_post_unknown_mission_confirm_returns_404() -> None:
    client = TestClient(app)

    response = client.post(f"/missions/{uuid4()}/confirm")

    assert response.status_code == 404


def test_post_mission_confirm_twice_returns_409() -> None:
    client = TestClient(app)
    mission_id = create_requires_confirmation_mission(client)
    client.post(f"/missions/{mission_id}/confirm")

    response = client.post(f"/missions/{mission_id}/confirm")

    assert response.status_code == 409


def test_post_completed_mission_run_returns_409() -> None:
    client = TestClient(app)
    mission_id = create_requires_confirmation_mission(client)
    client.post(f"/missions/{mission_id}/confirm")

    response = client.post(f"/missions/{mission_id}/run")

    assert response.status_code == 409
    assert "completed" in response.json()["detail"]

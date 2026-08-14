import asyncio
from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.dependencies import agent_task_repository, identity_repository
from app.domain.task import TaskStatus
from app.main import app


@pytest.fixture(autouse=True)
def clear_repositories() -> Iterator[None]:
    asyncio.run(agent_task_repository.clear())
    asyncio.run(identity_repository.clear())
    yield
    asyncio.run(agent_task_repository.clear())
    asyncio.run(identity_repository.clear())


def _create_person(client: TestClient) -> str:
    response = client.post(
        "/identities",
        json={
            "display_name": "Ivan Petrov",
            "first_name": "Ivan",
            "last_name": "Petrov",
            "birth_date": "1990-01-01",
            "documents": [
                {"type": "internal_passport", "number": "1234567890"}
            ],
        },
    )
    assert response.status_code == 200
    return response.json()["id"]


def test_create_and_list_universal_task() -> None:
    client = TestClient(app)
    person_id = _create_person(client)

    response = client.post(
        "/tasks",
        json={
            "instruction": "Купить два билета на вечерний спектакль",
            "target_url": "https://tickets.example.com/show/42",
            "person_ids": [person_id],
            "inferred_kind": "theatre-ticket",
        },
    )

    assert response.status_code == 201
    assert response.json()["status"] == "ready"
    assert response.json()["inferred_kind"] == "theatre_ticket"
    assert client.get("/tasks").json() == [response.json()]


def test_create_task_rejects_unknown_person() -> None:
    client = TestClient(app)

    response = client.post(
        "/tasks",
        json={
            "instruction": "Забронировать номер",
            "target_url": "https://hotel.example.com/",
            "person_ids": ["c3d4831d-213c-4d9f-b625-bf8406a5d4ea"],
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unknown_people"


def test_task_can_be_paused_resumed_and_cancelled() -> None:
    client = TestClient(app)
    person_id = _create_person(client)
    created = client.post(
        "/tasks",
        json={
            "instruction": "Найти авиабилет",
            "target_url": "https://air.example.com/",
            "person_ids": [person_id],
        },
    ).json()
    task_url = f"/tasks/{created['id']}"

    paused = client.post(f"{task_url}/pause")
    resumed = client.post(f"{task_url}/resume")
    cancelled = client.delete(task_url)

    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"
    assert paused.json()["version"] == 1
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "ready"
    assert resumed.json()["version"] == 2
    assert cancelled.status_code == 204
    assert client.get(task_url).json()["status"] == "cancelled"


def test_prepare_task_plan_persists_safe_preview() -> None:
    client = TestClient(app)
    person_id = _create_person(client)
    created = client.post(
        "/tasks",
        json={
            "instruction": "Купить авиабилет в Стамбул",
            "target_url": "https://air.example.com/",
            "person_ids": [person_id],
        },
    ).json()

    response = client.post(f"/tasks/{created['id']}/plan")
    stored = client.get(f"/tasks/{created['id']}").json()

    assert response.status_code == 200
    assert response.json()["inferred_kind"] == "flight_ticket"
    assert stored["inferred_kind"] == "flight_ticket"
    assert stored["plan"] == response.json()["plan"]
    assert stored["version"] == 1
    decisions = {
        item["step_id"]: item["decision"]
        for item in response.json()["permissions"]
    }
    assert decisions["fill_documents"]["requires_user"] is True
    assert decisions["prepare_order"]["reason"] == "confirmation_required"


def test_cancelled_task_cannot_be_planned() -> None:
    client = TestClient(app)
    person_id = _create_person(client)
    created = client.post(
        "/tasks",
        json={
            "instruction": "Купить билет в кино",
            "target_url": "https://cinema.example.com/",
            "person_ids": [person_id],
        },
    ).json()
    client.delete(f"/tasks/{created['id']}")

    response = client.post(f"/tasks/{created['id']}/plan")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "task_cannot_be_planned"


def test_completed_task_cannot_be_paused() -> None:
    client = TestClient(app)
    person_id = _create_person(client)
    created = client.post(
        "/tasks",
        json={
            "instruction": "Купить билет",
            "target_url": "https://cinema.example.com/",
            "person_ids": [person_id],
        },
    ).json()
    task = asyncio.run(agent_task_repository.get(UUID(created["id"])))
    assert task is not None
    asyncio.run(
        agent_task_repository.update(
            task.model_copy(update={"status": TaskStatus.COMPLETED}), task.version
        )
    )

    response = client.post(f"/tasks/{created['id']}/pause")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "invalid_task_transition"

import asyncio
from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.api.routes.tasks import _is_builtin_demo_url
from app.dependencies import agent_task_repository, identity_repository
from app.domain.browser_page import (
    BrowserControlKind,
    BrowserPageControl,
    BrowserPageSnapshot,
)
from app.domain.task import TaskStatus, UserActionReason
from app.domain.task_intent import TaskIntent
from app.domain.task_plan import TaskExecutionJournal, TaskJournalOutcome
from app.main import app
from app.services.task_executor import BrowserStepResult
from app.services.task_journal import append_task_journal_entry


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
    return str(response.json()["id"])


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://localhost:8000/demo/cinema", True),
        ("http://127.0.0.1:8000/demo/cinema", True),
        ("http://localhost:8000/ready", False),
        ("http://localhost:8000/demo/cinema?redirect=/admin", False),
        ("https://example.com/demo/cinema", False),
    ],
)
def test_only_exact_builtin_demo_url_can_use_local_browser_network(
    url: str,
    expected: bool,
) -> None:
    assert _is_builtin_demo_url(url) is expected


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


def test_step_by_step_task_executes_one_step_per_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SuccessfulBrowserRunner:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "SuccessfulBrowserRunner":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def run(self, task: object, step: object) -> BrowserStepResult:
            return BrowserStepResult(succeeded=True)

    monkeypatch.setattr(
        "app.api.routes.tasks.PlaywrightBrowserStepRunner",
        SuccessfulBrowserRunner,
    )
    client = TestClient(app)
    person_id = _create_person(client)
    created = client.post(
        "/tasks",
        json={
            "instruction": "Купить билет в кино",
            "target_url": "https://cinema.example.com/",
            "person_ids": [person_id],
            "control_mode": "step_by_step",
        },
    ).json()
    client.post(f"/tasks/{created['id']}/plan")

    response = client.post(f"/tasks/{created['id']}/execute")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    entries = response.json()["journal"]["entries"]
    assert [entry["step_id"] for entry in entries] == [
        "open_target",
        "open_target",
    ]


def test_plan_only_task_rejects_browser_execution() -> None:
    client = TestClient(app)
    person_id = _create_person(client)
    created = client.post(
        "/tasks",
        json={
            "instruction": "Купить билет в кино",
            "target_url": "https://cinema.example.com/",
            "person_ids": [person_id],
            "control_mode": "plan_only",
        },
    ).json()
    client.post(f"/tasks/{created['id']}/plan")

    response = client.post(f"/tasks/{created['id']}/execute")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "task_execution_disabled"


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
    assert stored["intent"] == response.json()["intent"]
    assert stored["intent"]["destination"] == "Стамбул"
    assert stored["plan"] == response.json()["plan"]
    assert stored["version"] == 1
    decisions = {
        item["step_id"]: item["decision"]
        for item in response.json()["permissions"]
    }
    assert decisions["fill_documents"]["requires_user"] is True
    assert decisions["open_review"]["allowed"] is True
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


def test_approve_currently_blocked_task_step() -> None:
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
    client.post(f"/tasks/{created['id']}/plan")
    task = asyncio.run(agent_task_repository.get(UUID(created["id"])))
    assert task is not None
    assert task.plan is not None
    journal = append_task_journal_entry(
        TaskExecutionJournal(task_id=task.id),
        task.plan,
        step_id="prepare_order",
        outcome=TaskJournalOutcome.BLOCKED,
        message="Execution paused for a required user action",
        timestamp=task.created_at,
        reason_code="confirmation_required",
    )
    waiting = task.model_copy(
        update={
            "status": TaskStatus.WAITING_FOR_USER,
            "waiting_reason": UserActionReason.CONFIRMATION_REQUIRED,
            "journal": journal,
        }
    )
    asyncio.run(agent_task_repository.update(waiting, task.version))

    response = client.post(
        f"/tasks/{task.id}/approvals",
        json={"step_id": "prepare-order"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["waiting_reason"] is None
    assert response.json()["approvals"][0]["step_id"] == "prepare_order"
    assert response.json()["approvals"][0]["consumed_at"] is None


def test_cannot_approve_a_step_that_is_not_blocked() -> None:
    client = TestClient(app)
    person_id = _create_person(client)
    created = client.post(
        "/tasks",
        json={
            "instruction": "Купить билет",
            "target_url": "https://tickets.example.com/",
            "person_ids": [person_id],
        },
    ).json()

    response = client.post(
        f"/tasks/{created['id']}/approvals",
        json={"step_id": "prepare_order"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "task_step_not_approvable"


def test_execute_task_runs_browser_driver_and_persists_journal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SuccessfulBrowserRunner:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        async def __aenter__(self) -> "SuccessfulBrowserRunner":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def run(self, task: object, step: object) -> BrowserStepResult:
            return BrowserStepResult(succeeded=True, reason_code="test_step_done")

    monkeypatch.setattr(
        "app.api.routes.tasks.PlaywrightBrowserStepRunner",
        SuccessfulBrowserRunner,
    )
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
    client.post(f"/tasks/{created['id']}/plan")

    response = client.post(f"/tasks/{created['id']}/execute")
    stored = client.get(f"/tasks/{created['id']}").json()

    assert response.status_code == 200
    assert response.json()["status"] == "waiting_for_user"
    assert response.json()["waiting_reason"] == "confirmation_required"
    assert stored["journal"] == response.json()["journal"]
    assert stored["version"] == 2


def test_map_page_persists_value_free_profile_bindings() -> None:
    client = TestClient(app)
    person_id = _create_person(client)
    created = client.post(
        "/tasks",
        json={
            "instruction": "Купить билет",
            "target_url": "https://tickets.example.com/",
            "person_ids": [person_id],
        },
    ).json()
    task = asyncio.run(agent_task_repository.get(UUID(created["id"])))
    assert task is not None
    snapshot = BrowserPageSnapshot(
        url="https://tickets.example.com/passenger",
        captured_at=task.created_at,
        controls=(
            BrowserPageControl(
                control_id="control_1",
                kind=BrowserControlKind.TEXT,
                label="First name",
                required=True,
            ),
            BrowserPageControl(
                control_id="control_2",
                kind=BrowserControlKind.TEXT,
                label="Passport number",
                required=True,
            ),
            BrowserPageControl(
                control_id="control_3",
                kind=BrowserControlKind.SELECT,
                label="Destination",
                options=("Kazan", "Moscow"),
                required=True,
            ),
        ),
    )
    asyncio.run(
        agent_task_repository.update(
            task.model_copy(
                update={
                    "page_snapshot": snapshot,
                    "intent": TaskIntent(
                        destination="Kazan",
                        participant_count=1,
                    ),
                }
            ),
            task.version,
        )
    )

    response = client.post(f"/tasks/{task.id}/map-page")

    assert response.status_code == 200
    fill_plan = response.json()["page_fill_plan"]
    assert [item["profile_field"] for item in fill_plan["bindings"]] == [
        "first_name",
        "document_number",
    ]
    assert fill_plan["bindings"][1]["sensitive"] is True
    assert fill_plan["intent_bindings"] == [
        {
            "control_id": "control_3",
            "intent_field": "destination",
            "search_term_index": None,
        }
    ]
    assert "Ivan" not in str(fill_plan)
    assert "1234567890" not in str(fill_plan)
    assert client.get(f"/tasks/{task.id}").json()["page_fill_plan"] == fill_plan


def test_map_page_requires_captured_page() -> None:
    client = TestClient(app)
    person_id = _create_person(client)
    created = client.post(
        "/tasks",
        json={
            "instruction": "Купить билет",
            "target_url": "https://tickets.example.com/",
            "person_ids": [person_id],
        },
    ).json()

    response = client.post(f"/tasks/{created['id']}/map-page")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "page_not_captured"


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

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domain.task import AgentTask, TaskStatus, UserActionReason

NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


def make_task(**overrides: object) -> AgentTask:
    values: dict[str, object] = {
        "id": uuid4(),
        "instruction": "Найди два билета и подготовь их к оплате",
        "target_url": "https://Tickets.Example/search?city=moscow",
        "person_ids": (uuid4(), uuid4()),
        "created_at": NOW,
    }
    values.update(overrides)
    return AgentTask.model_validate(values)


def test_agent_task_preserves_freeform_goal_and_selected_people() -> None:
    person_ids = (uuid4(), uuid4())

    task = make_task(
        instruction="  Найди   два билета\nи остановись перед оплатой  ",
        person_ids=person_ids,
        inferred_kind=" Event-Ticket ",
    )

    assert task.instruction == "Найди два билета и остановись перед оплатой"
    assert task.person_ids == person_ids
    assert task.inferred_kind == "event_ticket"
    assert task.status is TaskStatus.DRAFT
    assert task.target_url == "https://tickets.example/search?city=moscow"
    assert task.target_origin == "https://tickets.example"


@pytest.mark.parametrize(
    "target_url",
    [
        "tickets.example",
        "ftp://tickets.example/search",
        "http://tickets.example/search",
        "https://user:secret@tickets.example/search",
        "https://tickets.example/search#payment",
    ],
)
def test_agent_task_rejects_unsafe_or_non_absolute_target_url(
    target_url: str,
) -> None:
    with pytest.raises(ValidationError):
        make_task(target_url=target_url)


def test_agent_task_allows_local_http_executor() -> None:
    task = make_task(target_url="http://localhost:5173/form")

    assert task.target_origin == "http://localhost:5173"


def test_agent_task_rejects_duplicate_people() -> None:
    person_id = uuid4()

    with pytest.raises(ValidationError, match="duplicates"):
        make_task(person_ids=(person_id, person_id))


def test_waiting_task_requires_an_explicit_user_action_reason() -> None:
    with pytest.raises(ValidationError, match="waiting_reason"):
        make_task(status=TaskStatus.WAITING_FOR_USER)

    task = make_task(
        status=TaskStatus.WAITING_FOR_USER,
        waiting_reason=UserActionReason.PAYMENT_REQUIRED,
    )

    assert task.waiting_reason is UserActionReason.PAYMENT_REQUIRED


def test_non_waiting_task_rejects_stale_waiting_reason() -> None:
    with pytest.raises(ValidationError, match="waiting_reason"):
        make_task(
            status=TaskStatus.RUNNING,
            waiting_reason=UserActionReason.CAPTCHA_REQUIRED,
        )


def test_agent_task_requires_timezone_aware_creation_time() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        make_task(created_at=datetime(2026, 8, 13, 12))

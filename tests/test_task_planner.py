from datetime import UTC, datetime
from uuid import uuid4

from app.domain.task import AgentTask
from app.domain.task_permission import BrowserAction
from app.services.task_planner import build_task_plan, infer_task_kind

NOW = datetime(2026, 8, 14, 10, tzinfo=UTC)


def make_task(instruction: str, inferred_kind: str | None = None) -> AgentTask:
    return AgentTask(
        id=uuid4(),
        instruction=instruction,
        target_url="https://tickets.example.com/search",
        person_ids=(uuid4(),),
        inferred_kind=inferred_kind,
        created_at=NOW,
    )


def test_train_request_builds_profile_and_document_steps() -> None:
    preview = build_task_plan(
        make_task("Купить билет на поезд Москва — Казань"), NOW
    )

    assert preview.inferred_kind == "train_ticket"
    assert [step.action for step in preview.plan.steps] == [
        BrowserAction.NAVIGATE,
        BrowserAction.READ_PAGE,
        BrowserAction.FILL_BASIC_PROFILE,
        BrowserAction.FILL_SENSITIVE_PROFILE,
        BrowserAction.SELECT_OPTION,
        BrowserAction.SUBMIT_ORDER,
    ]
    assert preview.decisions[3].requires_user is True
    assert preview.decisions[-1].reason == "confirmation_required"


def test_cinema_request_avoids_unneeded_personal_data_steps() -> None:
    preview = build_task_plan(make_task("Купить билеты в кино"), NOW)

    assert preview.inferred_kind == "cinema_ticket"
    assert BrowserAction.FILL_BASIC_PROFILE not in {
        step.action for step in preview.plan.steps
    }
    assert BrowserAction.FILL_SENSITIVE_PROFILE not in {
        step.action for step in preview.plan.steps
    }


def test_replanning_increments_plan_version() -> None:
    task = make_task("Забронировать гостиницу")
    first = build_task_plan(task, NOW)
    second = build_task_plan(
        task.model_copy(update={"plan": first.plan}),
        NOW,
    )

    assert first.plan.version == 1
    assert second.plan.version == 2


def test_kind_inference_has_generic_fallback() -> None:
    assert infer_task_kind("Оформить выбранную покупку") == "generic_purchase"

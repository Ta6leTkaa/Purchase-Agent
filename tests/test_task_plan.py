from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.domain.task import AgentTask
from app.domain.task_permission import BrowserAction
from app.domain.task_plan import (
    TaskExecutionJournal,
    TaskJournalEntry,
    TaskJournalOutcome,
    TaskPlan,
    TaskPlanStep,
    TaskStepApproval,
)
from app.services.task_journal import append_task_journal_entry

NOW = datetime(2026, 8, 13, 15, tzinfo=UTC)


def make_steps() -> tuple[TaskPlanStep, ...]:
    return (
        TaskPlanStep(
            step_id="open-site",
            action=BrowserAction.NAVIGATE,
            summary="Открыть страницу поиска",
            target_url="https://tickets.example/search",
        ),
        TaskPlanStep(
            step_id="fill search",
            action=BrowserAction.FILL_BASIC_PROFILE,
            summary="  Заполнить   параметры поиска ",
            depends_on=("open_site",),
            requested_fields=("origin", "destination", "date"),
        ),
        TaskPlanStep(
            step_id="select_option",
            action=BrowserAction.SELECT_OPTION,
            summary="Выбрать подходящий вариант",
            depends_on=("fill search",),
        ),
    )


def make_plan(task_id: UUID | None = None) -> TaskPlan:
    return TaskPlan(
        task_id=task_id or uuid4(),
        created_at=NOW,
        steps=make_steps(),
    )


def test_plan_normalizes_steps_and_preserves_only_requested_field_names() -> None:
    plan = make_plan()

    assert [step.step_id for step in plan.steps] == [
        "open_site",
        "fill_search",
        "select_option",
    ]
    assert plan.steps[1].summary == "Заполнить параметры поиска"
    assert plan.steps[1].requested_fields == (
        "origin",
        "destination",
        "date",
    )
    assert plan.steps[0].to_action_request().target_url == (
        "https://tickets.example/search"
    )


def test_plan_rejects_duplicate_steps() -> None:
    step = make_steps()[0]

    with pytest.raises(ValidationError, match="unique"):
        TaskPlan(task_id=uuid4(), created_at=NOW, steps=(step, step))


def test_plan_rejects_forward_or_unknown_dependencies() -> None:
    with pytest.raises(ValidationError, match="earlier steps"):
        TaskPlan(
            task_id=uuid4(),
            created_at=NOW,
            steps=(
                TaskPlanStep(
                    step_id="select",
                    action=BrowserAction.SELECT_OPTION,
                    summary="Выбрать вариант",
                    depends_on=("search",),
                ),
            ),
        )


def test_plan_rejects_self_dependency() -> None:
    with pytest.raises(ValidationError, match="itself"):
        TaskPlan(
            task_id=uuid4(),
            created_at=NOW,
            steps=(
                TaskPlanStep(
                    step_id="search",
                    action=BrowserAction.READ_PAGE,
                    summary="Прочитать результаты",
                    depends_on=("search",),
                ),
            ),
        )


def test_agent_task_requires_matching_plan_and_journal() -> None:
    task_id = uuid4()
    plan = make_plan(task_id)
    journal = TaskExecutionJournal(task_id=task_id)

    task = AgentTask(
        id=task_id,
        instruction="Найди подходящий вариант",
        target_url="https://tickets.example",
        person_ids=(uuid4(),),
        created_at=NOW,
        plan=plan,
        journal=journal,
    )

    assert task.plan == plan
    assert task.journal == journal
    with pytest.raises(ValidationError, match="plan must belong"):
        task.model_copy(update={"plan": make_plan()}).model_validate(
            task.model_copy(update={"plan": make_plan()}).model_dump()
        )


def test_agent_task_rejects_journal_without_plan() -> None:
    task_id = uuid4()
    with pytest.raises(ValidationError, match="requires a task plan"):
        AgentTask(
            id=task_id,
            instruction="Найди вариант",
            target_url="https://tickets.example",
            person_ids=(uuid4(),),
            created_at=NOW,
            journal=TaskExecutionJournal(task_id=task_id),
        )


def test_journal_appends_contiguous_safe_entries() -> None:
    plan = make_plan()
    journal = TaskExecutionJournal(task_id=plan.task_id)

    started = append_task_journal_entry(
        journal,
        plan,
        step_id="open_site",
        outcome=TaskJournalOutcome.STARTED,
        message="  Открываем   страницу поиска ",
        timestamp=NOW,
    )
    succeeded = append_task_journal_entry(
        started,
        plan,
        step_id="open_site",
        outcome=TaskJournalOutcome.SUCCEEDED,
        message="Страница открыта",
        timestamp=NOW,
        reason_code="navigation_allowed",
    )

    assert journal.entries == ()
    assert [entry.sequence for entry in succeeded.entries] == [1, 2]
    assert succeeded.entries[0].message == "Открываем страницу поиска"
    assert succeeded.entries[1].reason_code == "navigation_allowed"


def test_journal_rejects_steps_outside_the_plan() -> None:
    plan = make_plan()

    with pytest.raises(ValueError, match="exist in the task plan"):
        append_task_journal_entry(
            TaskExecutionJournal(task_id=plan.task_id),
            plan,
            step_id="unknown",
            outcome=TaskJournalOutcome.FAILED,
            message="Неизвестный шаг",
            timestamp=NOW,
        )


def test_journal_rejects_non_contiguous_or_duplicate_events() -> None:
    event_id = uuid4()
    first = TaskJournalEntry(
        event_id=event_id,
        sequence=1,
        timestamp=NOW,
        step_id="open_site",
        outcome=TaskJournalOutcome.STARTED,
        message="Начато",
    )
    invalid_sequence = first.model_copy(update={"sequence": 3})

    with pytest.raises(ValidationError, match="contiguous"):
        TaskExecutionJournal(task_id=uuid4(), entries=(invalid_sequence,))
    with pytest.raises(ValidationError, match="unique"):
        TaskExecutionJournal(task_id=uuid4(), entries=(first, first))


def test_journal_forbids_arbitrary_metadata_that_could_contain_secrets() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        TaskJournalEntry.model_validate(
            {
                "event_id": uuid4(),
                "sequence": 1,
                "timestamp": NOW,
                "step_id": "fill_profile",
                "outcome": "succeeded",
                "message": "Профиль заполнен",
                "metadata": {"passport_number": "sensitive-value"},
            }
        )


def test_approval_is_bound_to_current_plan_step_and_version() -> None:
    task_id = uuid4()
    plan = make_plan(task_id)
    approval = TaskStepApproval(
        approval_id=uuid4(),
        plan_version=plan.version,
        step_id="select_option",
        reason="confirmation_required",
        approved_at=NOW,
    )

    task = AgentTask(
        id=task_id,
        instruction="Купить билет",
        target_url="https://tickets.example.com/",
        person_ids=(uuid4(),),
        created_at=NOW,
        plan=plan,
        approvals=(approval,),
    )

    assert task.approvals == (approval,)
    with pytest.raises(ValidationError, match="current plan version"):
        AgentTask.model_validate(
            task.model_copy(
                update={
                    "approvals": (
                        approval.model_copy(update={"plan_version": 2}),
                    )
                }
            ).model_dump()
        )

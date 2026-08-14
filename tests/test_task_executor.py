from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.browser_page import BrowserPageSnapshot
from app.domain.task import AgentTask, TaskStatus, UserActionReason
from app.domain.task_permission import BrowserAction, TaskPermissionPolicy
from app.domain.task_plan import TaskJournalOutcome, TaskPlanStep, TaskStepApproval
from app.services.task_executor import (
    BrowserStepResult,
    TaskExecutionError,
    execute_task_plan,
)
from app.services.task_planner import build_task_plan

NOW = datetime(2026, 8, 14, 12, tzinfo=UTC)


class RecordingRunner:
    def __init__(
        self,
        *,
        fail_on: str | None = None,
        page_snapshot: BrowserPageSnapshot | None = None,
    ) -> None:
        self.step_ids: list[str] = []
        self.fail_on = fail_on
        self.page_snapshot = page_snapshot

    async def run(
        self, task: AgentTask, step: TaskPlanStep
    ) -> BrowserStepResult:
        step_id = step.step_id
        self.step_ids.append(step_id)
        return BrowserStepResult(
            succeeded=step_id != self.fail_on,
            reason_code=("element_not_found" if step_id == self.fail_on else None),
            page_snapshot=(
                self.page_snapshot if step_id == "inspect_page" else None
            ),
        )


class IncrementingClock:
    def __init__(self) -> None:
        self.current = NOW

    def __call__(self) -> datetime:
        value = self.current
        self.current += timedelta(seconds=1)
        return value


def make_planned_task(
    instruction: str,
    *,
    permissions: TaskPermissionPolicy | None = None,
) -> AgentTask:
    task = AgentTask(
        id=uuid4(),
        instruction=instruction,
        target_url="https://tickets.example.com/search",
        person_ids=(uuid4(),),
        status=TaskStatus.READY,
        permissions=permissions or TaskPermissionPolicy(),
        created_at=NOW,
    )
    preview = build_task_plan(task, NOW)
    return task.model_copy(
        update={"plan": preview.plan, "inferred_kind": preview.inferred_kind}
    )


@pytest.mark.asyncio
async def test_executor_stops_before_sensitive_profile_data() -> None:
    task = make_planned_task("Купить билет на поезд")
    runner = RecordingRunner()

    result = await execute_task_plan(task, runner, IncrementingClock())

    assert result.status is TaskStatus.WAITING_FOR_USER
    assert result.waiting_reason is UserActionReason.SENSITIVE_DATA_APPROVAL_REQUIRED
    assert runner.step_ids == ["open_target", "inspect_page", "fill_people"]
    assert result.journal is not None
    assert result.journal.entries[-1].step_id == "fill_documents"
    assert result.journal.entries[-1].outcome is TaskJournalOutcome.BLOCKED
    assert all("123" not in entry.message for entry in result.journal.entries)


@pytest.mark.asyncio
async def test_executor_stops_before_irreversible_order_submission() -> None:
    task = make_planned_task("Купить билеты в кино")
    runner = RecordingRunner()

    result = await execute_task_plan(task, runner, IncrementingClock())

    assert result.status is TaskStatus.WAITING_FOR_USER
    assert result.waiting_reason is UserActionReason.CONFIRMATION_REQUIRED
    assert runner.step_ids == ["open_target", "inspect_page", "choose_option"]
    assert result.journal is not None
    assert result.journal.entries[-1].reason_code == "confirmation_required"


@pytest.mark.asyncio
async def test_executor_keeps_safe_page_snapshot() -> None:
    task = make_planned_task("Купить билеты в кино")
    snapshot = BrowserPageSnapshot(
        url=task.target_url,
        title="Ticket search",
        captured_at=NOW,
    )

    result = await execute_task_plan(
        task,
        RecordingRunner(page_snapshot=snapshot),
        IncrementingClock(),
    )

    assert result.page_snapshot == snapshot


@pytest.mark.asyncio
async def test_executor_consumes_one_time_approval_for_blocked_step() -> None:
    task = make_planned_task("Купить билеты в кино")
    blocked = await execute_task_plan(task, RecordingRunner(), IncrementingClock())
    assert blocked.plan is not None
    approval = TaskStepApproval(
        approval_id=uuid4(),
        plan_version=blocked.plan.version,
        step_id="prepare_order",
        reason="confirmation_required",
        approved_at=NOW,
    )
    approved = blocked.model_copy(
        update={
            "approvals": (approval,),
            "status": TaskStatus.READY,
            "waiting_reason": None,
        }
    )
    runner = RecordingRunner()

    result = await execute_task_plan(approved, runner, IncrementingClock())

    assert result.status is TaskStatus.PREPARED
    assert runner.step_ids == ["prepare_order"]
    assert result.approvals[0].consumed_at is not None
    assert result.journal is not None
    assert result.journal.entries[-2].reason_code == "user_approved"


@pytest.mark.asyncio
async def test_executor_records_browser_failure_without_exception_details() -> None:
    task = make_planned_task("Купить билеты в кино")
    runner = RecordingRunner(fail_on="inspect_page")

    result = await execute_task_plan(task, runner, IncrementingClock())

    assert result.status is TaskStatus.FAILED
    assert runner.step_ids == ["open_target", "inspect_page"]
    assert result.journal is not None
    assert result.journal.entries[-1].message == "Browser step failed"
    assert result.journal.entries[-1].reason_code == "element_not_found"


@pytest.mark.asyncio
async def test_executor_denies_disabled_actions_without_calling_runner() -> None:
    task = make_planned_task(
        "Купить билеты в кино",
        permissions=TaskPermissionPolicy(allow_navigation=False),
    )
    runner = RecordingRunner()

    result = await execute_task_plan(task, runner, IncrementingClock())

    assert result.status is TaskStatus.FAILED
    assert runner.step_ids == []
    assert result.journal is not None
    assert result.journal.entries[-1].reason_code == "navigation_not_allowed"


@pytest.mark.asyncio
async def test_executor_skips_steps_that_already_succeeded() -> None:
    task = make_planned_task("Купить билеты в кино")
    first_runner = RecordingRunner(fail_on="choose_option")
    failed = await execute_task_plan(task, first_runner, IncrementingClock())
    resumed = failed.model_copy(update={"status": TaskStatus.READY})
    second_runner = RecordingRunner()

    result = await execute_task_plan(resumed, second_runner, IncrementingClock())

    assert second_runner.step_ids == ["choose_option"]
    assert result.status is TaskStatus.WAITING_FOR_USER


@pytest.mark.asyncio
async def test_executor_requires_plan_and_executable_status() -> None:
    unplanned = AgentTask(
        id=uuid4(),
        instruction="Купить билет",
        target_url="https://tickets.example.com/",
        person_ids=(uuid4(),),
        created_at=NOW,
    )
    runner = RecordingRunner()

    with pytest.raises(TaskExecutionError, match="must have a plan"):
        await execute_task_plan(unplanned, runner, IncrementingClock())
    with pytest.raises(TaskExecutionError, match="cannot be executed"):
        await execute_task_plan(
            make_planned_task("Купить билет").model_copy(
                update={"status": TaskStatus.CANCELLED}
            ),
            runner,
            IncrementingClock(),
        )


def test_planner_never_places_payment_in_automatic_plan() -> None:
    task = make_planned_task("Купить авиабилет")

    assert task.plan is not None
    assert BrowserAction.PAY not in {step.action for step in task.plan.steps}

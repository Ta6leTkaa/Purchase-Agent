from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.domain.browser_page import BrowserPageSnapshot
from app.domain.task import AgentTask, TaskStatus, UserActionReason
from app.domain.task_plan import (
    TaskExecutionJournal,
    TaskJournalOutcome,
    TaskPlanStep,
)
from app.services.task_journal import append_task_journal_entry
from app.services.task_permission import evaluate_browser_action


class BrowserStepResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    succeeded: bool
    reason_code: str | None = Field(
        default=None,
        max_length=100,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    page_snapshot: BrowserPageSnapshot | None = None


class BrowserStepRunner(Protocol):
    async def run(self, task: AgentTask, step: TaskPlanStep) -> BrowserStepResult: ...


class TaskExecutionError(Exception):
    pass


async def execute_task_plan(
    task: AgentTask,
    runner: BrowserStepRunner,
    now: Callable[[], datetime],
) -> AgentTask:
    """Execute safe steps and stop before every user-controlled boundary."""
    if task.plan is None:
        raise TaskExecutionError("task must have a plan before execution")
    if task.status not in {TaskStatus.READY, TaskStatus.RUNNING}:
        raise TaskExecutionError(
            f"task in {task.status.value} state cannot be executed"
        )
    journal = task.journal or TaskExecutionJournal(task_id=task.id)
    succeeded_steps = {
        entry.step_id
        for entry in journal.entries
        if entry.outcome is TaskJournalOutcome.SUCCEEDED
    }
    current = task.model_copy(
        update={
            "status": TaskStatus.RUNNING,
            "waiting_reason": None,
            "journal": journal,
        }
    )
    for step in task.plan.steps:
        if step.step_id in succeeded_steps:
            continue
        decision = evaluate_browser_action(current, step.to_action_request())
        approval = next(
            (
                item
                for item in current.approvals
                if item.plan_version == task.plan.version
                and item.step_id == step.step_id
                and item.reason == decision.reason
                and item.consumed_at is None
            ),
            None,
        )
        approved = decision.requires_user and approval is not None
        if decision.requires_user and not approved:
            journal = append_task_journal_entry(
                journal,
                task.plan,
                step_id=step.step_id,
                outcome=TaskJournalOutcome.BLOCKED,
                message="Execution paused for a required user action",
                timestamp=now(),
                reason_code=decision.reason,
            )
            return current.model_copy(
                update={
                    "status": TaskStatus.WAITING_FOR_USER,
                    "waiting_reason": UserActionReason(decision.reason),
                    "journal": journal,
                }
            )
        if not decision.allowed and not approved:
            journal = append_task_journal_entry(
                journal,
                task.plan,
                step_id=step.step_id,
                outcome=TaskJournalOutcome.FAILED,
                message="Execution denied by the task permission policy",
                timestamp=now(),
                reason_code=decision.reason,
            )
            return current.model_copy(
                update={"status": TaskStatus.FAILED, "journal": journal}
            )
        started_at = now()
        if approval is not None:
            consumed = approval.model_copy(update={"consumed_at": started_at})
            current = current.model_copy(
                update={
                    "approvals": tuple(
                        consumed if item.approval_id == approval.approval_id else item
                        for item in current.approvals
                    )
                }
            )
        journal = append_task_journal_entry(
            journal,
            task.plan,
            step_id=step.step_id,
            outcome=TaskJournalOutcome.STARTED,
            message="Browser step started",
            timestamp=started_at,
            reason_code=("user_approved" if approved else decision.reason),
        )
        current = current.model_copy(update={"journal": journal})
        try:
            result = await runner.run(current, step)
        except Exception:
            result = BrowserStepResult(
                succeeded=False,
                reason_code="browser_step_exception",
            )
        outcome = (
            TaskJournalOutcome.SUCCEEDED
            if result.succeeded
            else TaskJournalOutcome.FAILED
        )
        journal = append_task_journal_entry(
            journal,
            task.plan,
            step_id=step.step_id,
            outcome=outcome,
            message=(
                "Browser step completed"
                if result.succeeded
                else "Browser step failed"
            ),
            timestamp=now(),
            reason_code=result.reason_code,
        )
        current = current.model_copy(update={"journal": journal})
        if result.page_snapshot is not None:
            current = current.model_copy(
                update={"page_snapshot": result.page_snapshot}
            )
        if not result.succeeded:
            return current.model_copy(update={"status": TaskStatus.FAILED})
        succeeded_steps.add(step.step_id)
    return current.model_copy(update={"status": TaskStatus.PREPARED})

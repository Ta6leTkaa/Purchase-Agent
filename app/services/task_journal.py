from datetime import datetime
from uuid import UUID, uuid4

from app.domain.task_plan import (
    TaskExecutionJournal,
    TaskJournalEntry,
    TaskJournalOutcome,
    TaskPlan,
)


def append_task_journal_entry(
    journal: TaskExecutionJournal,
    plan: TaskPlan,
    *,
    step_id: str,
    outcome: TaskJournalOutcome,
    message: str,
    timestamp: datetime,
    reason_code: str | None = None,
    event_id: UUID | None = None,
) -> TaskExecutionJournal:
    if journal.task_id != plan.task_id:
        raise ValueError("journal and plan must belong to the same task")
    plan_step_ids = {step.step_id for step in plan.steps}
    if step_id not in plan_step_ids:
        raise ValueError("journal step must exist in the task plan")
    entry = TaskJournalEntry(
        event_id=event_id or uuid4(),
        sequence=len(journal.entries) + 1,
        timestamp=timestamp,
        step_id=step_id,
        outcome=outcome,
        message=message,
        reason_code=reason_code,
    )
    return TaskExecutionJournal(
        task_id=journal.task_id,
        entries=(*journal.entries, entry),
    )

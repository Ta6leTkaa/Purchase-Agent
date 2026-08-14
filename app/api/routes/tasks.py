from datetime import datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.adapters.playwright_browser import PlaywrightBrowserStepRunner
from app.api.dependencies.auth import require_api_key
from app.core.config import settings
from app.dependencies import (
    get_agent_task_repository,
    get_current_time,
    get_identity_repository,
)
from app.domain.task import AgentTask, TaskStatus, UserActionReason
from app.domain.task_plan import TaskJournalOutcome, TaskStepApproval
from app.repositories.identity import IdentityRepository
from app.repositories.task import AgentTaskRepository, AgentTaskVersionConflictError
from app.schemas.task import (
    AgentTaskCreate,
    TaskPlanResponse,
    TaskPlanStepPreview,
    TaskStepApprovalCreate,
)
from app.services.clock import utc_now
from app.services.task_executor import TaskExecutionError, execute_task_plan
from app.services.task_planner import build_task_plan

router = APIRouter(
    prefix="/tasks", tags=["tasks"], dependencies=[Depends(require_api_key)]
)
TaskRepositoryDep = Annotated[AgentTaskRepository, Depends(get_agent_task_repository)]
IdentityRepositoryDep = Annotated[IdentityRepository, Depends(get_identity_repository)]
CurrentTimeDep = Annotated[datetime, Depends(get_current_time)]


@router.post("", status_code=201)
async def create_task(
    request: AgentTaskCreate,
    tasks: TaskRepositoryDep,
    identities: IdentityRepositoryDep,
    now: CurrentTimeDep,
) -> AgentTask:
    missing = [
        person_id
        for person_id in request.person_ids
        if await identities.get(person_id) is None
    ]
    if missing:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "unknown_people",
                "message": "Every selected person must exist.",
                "person_ids": [str(person_id) for person_id in missing],
            },
        )
    task = AgentTask(
        id=uuid4(),
        instruction=request.instruction,
        target_url=request.target_url,
        person_ids=request.person_ids,
        inferred_kind=request.inferred_kind,
        permissions=request.permissions,
        status=TaskStatus.READY,
        created_at=now,
    )
    return await tasks.create(task)


@router.get("")
async def list_tasks(
    tasks: TaskRepositoryDep,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[AgentTask]:
    return await tasks.list(limit=limit)


@router.get("/{task_id}")
async def get_task(task_id: UUID, tasks: TaskRepositoryDep) -> AgentTask:
    return await _require_task(tasks, task_id)


@router.post("/{task_id}/plan")
async def prepare_task_plan(
    task_id: UUID,
    tasks: TaskRepositoryDep,
    now: CurrentTimeDep,
) -> TaskPlanResponse:
    task = await _require_task(tasks, task_id)
    if task.status in {
        TaskStatus.RUNNING,
        TaskStatus.COMPLETED,
        TaskStatus.CANCELLED,
    }:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "task_cannot_be_planned",
                "message": f"Task in {task.status.value} state cannot be planned.",
            },
        )
    preview = build_task_plan(task, now)
    await _update_task(
        tasks,
        task,
        task.model_copy(
            update={
                "plan": preview.plan,
                "journal": None,
                "approvals": (),
                "inferred_kind": preview.inferred_kind,
                "status": TaskStatus.READY,
                "waiting_reason": None,
            }
        ),
    )
    return TaskPlanResponse(
        inferred_kind=preview.inferred_kind,
        plan=preview.plan,
        permissions=tuple(
            TaskPlanStepPreview(step_id=step.step_id, decision=decision)
            for step, decision in zip(
                preview.plan.steps, preview.decisions, strict=True
            )
        ),
    )


@router.post("/{task_id}/approvals")
async def approve_task_step(
    task_id: UUID,
    request: TaskStepApprovalCreate,
    tasks: TaskRepositoryDep,
    now: CurrentTimeDep,
) -> AgentTask:
    task = await _require_task(tasks, task_id)
    if (
        task.status is not TaskStatus.WAITING_FOR_USER
        or task.waiting_reason is None
        or task.plan is None
        or task.journal is None
        or not task.journal.entries
    ):
        raise _approval_conflict("Task is not waiting for an approvable step.")
    blocked = task.journal.entries[-1]
    if (
        blocked.outcome is not TaskJournalOutcome.BLOCKED
        or blocked.step_id != request.step_id.strip().casefold().replace("-", "_")
    ):
        raise _approval_conflict("Only the currently blocked step can be approved.")
    if blocked.reason_code != task.waiting_reason.value:
        raise _approval_conflict("Blocked step reason no longer matches the task.")
    if any(
        approval.step_id == blocked.step_id and approval.consumed_at is None
        for approval in task.approvals
    ):
        raise _approval_conflict("The blocked step already has an active approval.")
    approval = TaskStepApproval(
        approval_id=uuid4(),
        plan_version=task.plan.version,
        step_id=blocked.step_id,
        reason=UserActionReason(blocked.reason_code).value,
        approved_at=now,
    )
    changed = task.model_copy(
        update={
            "approvals": (*task.approvals, approval),
            "status": TaskStatus.READY,
            "waiting_reason": None,
        }
    )
    return await _update_task(tasks, task, changed)


@router.post("/{task_id}/execute")
async def execute_task(task_id: UUID, tasks: TaskRepositoryDep) -> AgentTask:
    if not settings.browser_automation_enabled:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "browser_automation_disabled",
                "message": "Browser automation is disabled.",
            },
        )
    task = await _require_task(tasks, task_id)
    try:
        async with PlaywrightBrowserStepRunner(
            timeout_seconds=settings.browser_navigation_timeout_seconds,
            allow_local_network=settings.environment != "production",
        ) as runner:
            executed = await execute_task_plan(task, runner, utc_now)
    except TaskExecutionError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "task_not_executable", "message": str(exc)},
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "browser_driver_unavailable",
                "message": "Browser driver could not be started.",
            },
        ) from exc
    return await _update_task(tasks, task, executed)


@router.post("/{task_id}/pause")
async def pause_task(task_id: UUID, tasks: TaskRepositoryDep) -> AgentTask:
    task = await _require_task(tasks, task_id)
    if task.status in {TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.PAUSED}:
        raise _invalid_transition(task.status, TaskStatus.PAUSED)
    return await _update_status(tasks, task, TaskStatus.PAUSED)


@router.post("/{task_id}/resume")
async def resume_task(task_id: UUID, tasks: TaskRepositoryDep) -> AgentTask:
    task = await _require_task(tasks, task_id)
    if task.status not in {
        TaskStatus.PAUSED,
        TaskStatus.FAILED,
        TaskStatus.WAITING_FOR_USER,
    }:
        raise _invalid_transition(task.status, TaskStatus.READY)
    return await _update_status(tasks, task, TaskStatus.READY)


@router.delete("/{task_id}", status_code=204)
async def cancel_task(task_id: UUID, tasks: TaskRepositoryDep) -> Response:
    task = await _require_task(tasks, task_id)
    if task.status is TaskStatus.COMPLETED:
        raise _invalid_transition(task.status, TaskStatus.CANCELLED)
    if task.status is not TaskStatus.CANCELLED:
        await _update_status(tasks, task, TaskStatus.CANCELLED)
    return Response(status_code=204)


async def _require_task(tasks: AgentTaskRepository, task_id: UUID) -> AgentTask:
    task = await tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


async def _update_status(
    tasks: AgentTaskRepository, task: AgentTask, status: TaskStatus
) -> AgentTask:
    changed = task.model_copy(update={"status": status, "waiting_reason": None})
    return await _update_task(tasks, task, changed)


async def _update_task(
    tasks: AgentTaskRepository,
    task: AgentTask,
    changed: AgentTask,
) -> AgentTask:
    try:
        updated = await tasks.update(changed, task.version)
    except AgentTaskVersionConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "task_version_conflict",
                "message": "Task changed concurrently.",
            },
        ) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return updated


def _invalid_transition(current: TaskStatus, target: TaskStatus) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "invalid_task_transition",
            "message": f"Task cannot move from {current.value} to {target.value}.",
        },
    )


def _approval_conflict(message: str) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={"code": "task_step_not_approvable", "message": message},
    )

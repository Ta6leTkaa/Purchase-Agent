from datetime import datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.api.dependencies.auth import require_api_key
from app.dependencies import (
    get_agent_task_repository,
    get_current_time,
    get_identity_repository,
)
from app.domain.task import AgentTask, TaskStatus
from app.repositories.identity import IdentityRepository
from app.repositories.task import AgentTaskRepository, AgentTaskVersionConflictError
from app.schemas.task import AgentTaskCreate

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

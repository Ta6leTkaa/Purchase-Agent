import logging
from datetime import datetime
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.adapters.openai_agent import (
    AgentDecisionProviderError,
    OpenAIAgentDecisionProvider,
)
from app.adapters.playwright_browser import (
    PlaywrightBrowserStepRunner,
    VisibleBrowserUnavailableError,
)
from app.api.dependencies.auth import require_api_key
from app.core.config import settings
from app.dependencies import (
    get_agent_task_repository,
    get_current_time,
    get_identity_repository,
)
from app.domain.agent_run import (
    AgentLoopResult,
    AgentLoopStatus,
    merge_agent_loop_results,
)
from app.domain.browser_command import AskUserCommand
from app.domain.identity import Identity
from app.domain.task import AgentTask, TaskClarification, TaskStatus, UserActionReason
from app.domain.task_plan import TaskJournalOutcome, TaskStepApproval
from app.repositories.identity import IdentityRepository
from app.repositories.task import AgentTaskRepository, AgentTaskVersionConflictError
from app.schemas.task import (
    AgentTaskCreate,
    TaskClarificationCreate,
    TaskPlanResponse,
    TaskPlanStepPreview,
    TaskStepApprovalCreate,
)
from app.services.agent_loop import run_agent_loop
from app.services.clock import utc_now
from app.services.page_field_mapper import build_page_fill_plan
from app.services.task_executor import TaskExecutionError, execute_task_plan
from app.services.task_planner import build_task_plan

logger = logging.getLogger(__name__)

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
        control_mode=request.control_mode,
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
                "page_snapshot": None,
                "page_fill_plan": None,
                "inferred_kind": preview.inferred_kind,
                "intent": preview.intent,
                "status": TaskStatus.READY,
                "waiting_reason": None,
            }
        ),
    )
    return TaskPlanResponse(
        inferred_kind=preview.inferred_kind,
        intent=preview.intent,
        plan=preview.plan,
        permissions=tuple(
            TaskPlanStepPreview(step_id=step.step_id, decision=decision)
            for step, decision in zip(
                preview.plan.steps, preview.decisions, strict=True
            )
        ),
    )


@router.post("/{task_id}/map-page")
async def map_task_page(
    task_id: UUID,
    tasks: TaskRepositoryDep,
    identities: IdentityRepositoryDep,
    now: CurrentTimeDep,
) -> AgentTask:
    task = await _require_task(tasks, task_id)
    if task.page_snapshot is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "page_not_captured",
                "message": "Execute page inspection before mapping fields.",
            },
        )
    people = await _load_task_people(task, identities)
    fill_plan = build_page_fill_plan(task, people, now)
    return await _update_task(
        tasks,
        task,
        task.model_copy(update={"page_fill_plan": fill_plan}),
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


@router.post("/{task_id}/clarifications")
async def clarify_agent_task(
    task_id: UUID,
    request: TaskClarificationCreate,
    tasks: TaskRepositoryDep,
    now: CurrentTimeDep,
) -> AgentTask:
    task = await _require_task(tasks, task_id)
    question = _pending_agent_question(task)
    if (
        task.status is not TaskStatus.WAITING_FOR_USER
        or task.waiting_reason is not UserActionReason.CLARIFICATION_REQUIRED
        or question is None
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "task_clarification_not_expected",
                "message": "The agent is not waiting for a clarification.",
            },
        )
    changed = task.model_copy(
        update={
            "clarifications": (
                *task.clarifications,
                TaskClarification(
                    question=question,
                    answer=request.answer,
                    created_at=now,
                ),
            ),
            "status": TaskStatus.READY,
            "waiting_reason": None,
        }
    )
    return await _update_task(tasks, task, changed)


@router.post("/{task_id}/execute")
async def execute_task(
    task_id: UUID,
    tasks: TaskRepositoryDep,
    identities: IdentityRepositoryDep,
) -> AgentTask:
    if not settings.browser_automation_enabled:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "browser_automation_disabled",
                "message": "Browser automation is disabled.",
            },
        )
    task = await _require_task(tasks, task_id)
    if task.control_mode.value == "plan_only":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "task_execution_disabled",
                "message": "Task is in plan-only mode.",
            },
        )
    if task.status is TaskStatus.WAITING_FOR_USER and task.waiting_reason in {
        UserActionReason.AUTHENTICATION_REQUIRED,
        UserActionReason.CAPTCHA_REQUIRED,
        UserActionReason.CONFIRMATION_REQUIRED,
        UserActionReason.PAYMENT_REQUIRED,
    }:
        task = task.model_copy(
            update={"status": TaskStatus.READY, "waiting_reason": None}
        )
    people = await _load_task_people(task, identities)
    if settings.agent_llm_enabled:
        return await _execute_llm_agent(task, tasks, people)
    try:
        async with PlaywrightBrowserStepRunner(
            timeout_seconds=settings.browser_navigation_timeout_seconds,
            allow_local_network=(
                settings.environment != "production"
                or _is_builtin_demo_url(task.target_url)
            ),
            identities=tuple(people),
            cdp_url=settings.browser_cdp_url,
        ) as runner:
            executed = await execute_task_plan(
                task,
                runner,
                utc_now,
                max_steps=(1 if task.control_mode.value == "step_by_step" else None),
            )
    except TaskExecutionError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "task_not_executable", "message": str(exc)},
        ) from exc
    except VisibleBrowserUnavailableError as exc:
        raise _visible_browser_unavailable() from exc
    except Exception as exc:
        logger.exception("Browser plan execution failed for task %s", task.id)
        raise HTTPException(
            status_code=503,
            detail={
                "code": "browser_driver_unavailable",
                "message": "Browser driver could not be started.",
            },
        ) from exc
    return await _update_task(tasks, task, executed)


async def _execute_llm_agent(
    task: AgentTask,
    tasks: AgentTaskRepository,
    people: list[Identity],
) -> AgentTask:
    if task.status not in {TaskStatus.READY, TaskStatus.RUNNING}:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "task_not_executable",
                "message": f"Task in {task.status.value} state cannot be executed.",
            },
        )
    if settings.openai_api_key is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "agent_llm_not_configured",
                "message": "LLM agent is enabled but OPENAI_API_KEY is missing.",
            },
        )
    try:
        async with PlaywrightBrowserStepRunner(
            timeout_seconds=settings.browser_navigation_timeout_seconds,
            allow_local_network=(
                settings.environment != "production"
                or _is_builtin_demo_url(task.target_url)
            ),
            identities=tuple(people),
            cdp_url=settings.browser_cdp_url,
        ) as runner:
            async with OpenAIAgentDecisionProvider(
                api_key=settings.openai_api_key,
                model=settings.agent_llm_model,
                reasoning_effort=settings.agent_llm_reasoning_effort,
                timeout_seconds=settings.agent_llm_timeout_seconds,
            ) as provider:
                result = await run_agent_loop(
                    task,
                    provider=provider,
                    runtime=runner,
                    max_steps=(
                        1
                        if task.control_mode.value == "step_by_step"
                        else settings.agent_llm_max_steps
                    ),
                )
    except AgentDecisionProviderError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "agent_llm_unavailable",
                "message": "The LLM agent could not choose its next action.",
            },
        ) from exc
    except VisibleBrowserUnavailableError as exc:
        raise _visible_browser_unavailable() from exc
    except Exception as exc:
        logger.exception("LLM browser execution failed for task %s", task.id)
        raise HTTPException(
            status_code=503,
            detail={
                "code": "browser_driver_unavailable",
                "message": "Browser driver could not complete the agent run.",
            },
        ) from exc
    changed = _apply_agent_result(task, result)
    return await _update_task(tasks, task, changed)


def _visible_browser_unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "code": "visible_browser_unavailable",
            "message": (
                "Окно агента закрыто. Запустите видимый Chromium и повторите шаг."
            ),
        },
    )


def _apply_agent_result(task: AgentTask, result: AgentLoopResult) -> AgentTask:
    if result.status is AgentLoopStatus.COMPLETED:
        status = TaskStatus.COMPLETED
        waiting_reason = None
    elif result.status is AgentLoopStatus.WAITING_FOR_USER:
        status = TaskStatus.WAITING_FOR_USER
        waiting_reason = _agent_waiting_reason(result.reason_code)
    elif (
        result.status is AgentLoopStatus.EXHAUSTED
        and task.control_mode.value == "step_by_step"
    ):
        status = TaskStatus.READY
        waiting_reason = None
    else:
        status = TaskStatus.FAILED
        waiting_reason = None
    persisted_result = merge_agent_loop_results(task.agent_run, result)
    return task.model_copy(
        update={
            "status": status,
            "waiting_reason": waiting_reason,
            "page_snapshot": result.page_snapshot,
            "page_fill_plan": None,
            "agent_run": persisted_result,
        }
    )


def _agent_waiting_reason(reason_code: str) -> UserActionReason:
    reasons = {
        "sensitive_data_approval_required": (
            UserActionReason.SENSITIVE_DATA_APPROVAL_REQUIRED
        ),
        "authentication_required": UserActionReason.AUTHENTICATION_REQUIRED,
        "captcha_required": UserActionReason.CAPTCHA_REQUIRED,
        "payment_required": UserActionReason.PAYMENT_REQUIRED,
        "irreversible_click_requires_user": UserActionReason.CONFIRMATION_REQUIRED,
        "finished_ready_for_user": UserActionReason.CONFIRMATION_REQUIRED,
    }
    return reasons.get(reason_code, UserActionReason.CLARIFICATION_REQUIRED)


def _pending_agent_question(task: AgentTask) -> str | None:
    if task.agent_run is None or not task.agent_run.steps:
        return None
    command = task.agent_run.steps[-1].decision.command
    return command.question if isinstance(command, AskUserCommand) else None


def _is_builtin_demo_url(url: str) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        and parsed.path in {"/demo/cinema", "/demo/hotel"}
        and not parsed.query
        and not parsed.fragment
    )


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
        TaskStatus.MONITORING,
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


async def _load_task_people(
    task: AgentTask,
    identities: IdentityRepository,
) -> list[Identity]:
    people: list[Identity] = []
    for person_id in task.person_ids:
        identity = await identities.get(person_id)
        if identity is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "person_no_longer_exists",
                    "message": "A selected person no longer exists.",
                },
            )
        people.append(identity)
    return people


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

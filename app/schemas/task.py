from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.task import TaskControlMode
from app.domain.task_intent import TaskIntent
from app.domain.task_permission import PermissionDecision, TaskPermissionPolicy
from app.domain.task_plan import TaskPlan


class AgentTaskCreate(BaseModel):
    instruction: str = Field(min_length=1, max_length=4_000)
    target_url: str = Field(min_length=1, max_length=2_048)
    person_ids: tuple[UUID, ...] = Field(min_length=1, max_length=100)
    inferred_kind: str | None = Field(default=None, max_length=64)
    permissions: TaskPermissionPolicy = TaskPermissionPolicy()
    control_mode: TaskControlMode = TaskControlMode.SAFE_AUTO


class TaskPlanStepPreview(BaseModel):
    step_id: str
    decision: PermissionDecision


class TaskPlanResponse(BaseModel):
    inferred_kind: str
    intent: TaskIntent
    plan: TaskPlan
    permissions: tuple[TaskPlanStepPreview, ...]


class TaskStepApprovalCreate(BaseModel):
    step_id: str = Field(min_length=1, max_length=64)


class TaskClarificationCreate(BaseModel):
    answer: str = Field(min_length=1, max_length=2_000)

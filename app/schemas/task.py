from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.task_permission import TaskPermissionPolicy


class AgentTaskCreate(BaseModel):
    instruction: str = Field(min_length=1, max_length=4_000)
    target_url: str = Field(min_length=1, max_length=2_048)
    person_ids: tuple[UUID, ...] = Field(min_length=1, max_length=100)
    inferred_kind: str | None = Field(default=None, max_length=64)
    permissions: TaskPermissionPolicy = TaskPermissionPolicy()

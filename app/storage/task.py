import builtins
from uuid import UUID

from app.domain.task import AgentTask
from app.repositories.task import AgentTaskVersionConflictError


class InMemoryAgentTaskRepository:
    def __init__(self) -> None:
        self._tasks: dict[UUID, AgentTask] = {}

    async def create(self, task: AgentTask) -> AgentTask:
        self._tasks[task.id] = task
        return task

    async def get(self, task_id: UUID) -> AgentTask | None:
        return self._tasks.get(task_id)

    async def list(self, *, limit: int = 100) -> builtins.list[AgentTask]:
        if limit <= 0:
            raise ValueError("limit must be greater than 0")
        return sorted(
            self._tasks.values(), key=lambda task: (task.created_at, task.id)
        )[:limit]

    async def update(self, task: AgentTask, expected_version: int) -> AgentTask | None:
        current = self._tasks.get(task.id)
        if current is None:
            return None
        if current.version != expected_version:
            raise AgentTaskVersionConflictError
        updated = task.model_copy(update={"version": expected_version + 1})
        self._tasks[task.id] = updated
        return updated

    async def clear(self) -> None:
        self._tasks.clear()

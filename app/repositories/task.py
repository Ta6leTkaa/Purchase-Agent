import builtins
from typing import Protocol
from uuid import UUID

from app.domain.task import AgentTask


class AgentTaskVersionConflictError(Exception):
    pass


class AgentTaskRepository(Protocol):
    async def create(self, task: AgentTask) -> AgentTask: ...

    async def get(self, task_id: UUID) -> AgentTask | None: ...

    async def list(self, *, limit: int = 100) -> builtins.list[AgentTask]: ...

    async def update(
        self, task: AgentTask, expected_version: int
    ) -> AgentTask | None: ...

    async def clear(self) -> None: ...

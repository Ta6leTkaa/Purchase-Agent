import builtins
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.task import (
    AgentTaskModel,
    agent_task_from_model,
    agent_task_to_model,
)
from app.domain.task import AgentTask
from app.repositories.task import AgentTaskRepository, AgentTaskVersionConflictError


class SqlAlchemyAgentTaskRepository(AgentTaskRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, task: AgentTask) -> AgentTask:
        model = agent_task_to_model(task)
        self._session.add(model)
        await self._session.flush()
        return agent_task_from_model(model)

    async def get(self, task_id: UUID) -> AgentTask | None:
        model = await self._session.scalar(
            select(AgentTaskModel).where(AgentTaskModel.id == task_id)
        )
        return agent_task_from_model(model) if model is not None else None

    async def list(self, *, limit: int = 100) -> builtins.list[AgentTask]:
        if limit <= 0:
            raise ValueError("limit must be greater than 0")
        result = await self._session.scalars(
            select(AgentTaskModel)
            .order_by(AgentTaskModel.created_at, AgentTaskModel.id)
            .limit(limit)
        )
        return [agent_task_from_model(model) for model in result.all()]

    async def update(self, task: AgentTask, expected_version: int) -> AgentTask | None:
        data = task.model_dump(mode="json")
        result = await self._session.execute(
            update(AgentTaskModel)
            .where(AgentTaskModel.id == task.id)
            .where(AgentTaskModel.version == expected_version)
            .values(
                status=task.status.value,
                control_mode=task.control_mode.value,
                inferred_kind=task.inferred_kind,
                intent=data["intent"],
                waiting_reason=(
                    task.waiting_reason.value if task.waiting_reason else None
                ),
                permissions=data["permissions"],
                plan=data["plan"],
                journal=data["journal"],
                approvals=data["approvals"],
                page_snapshot=data["page_snapshot"],
                page_fill_plan=data["page_fill_plan"],
                version=expected_version + 1,
            )
            .returning(AgentTaskModel.id)
        )
        if result.scalar_one_or_none() is None:
            if await self.get(task.id) is None:
                return None
            raise AgentTaskVersionConflictError
        await self._session.flush()
        return task.model_copy(update={"version": expected_version + 1})

    async def clear(self) -> None:
        await self._session.execute(delete(AgentTaskModel))
        await self._session.flush()

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.task import AgentTask, TaskStatus
from app.repositories.sqlalchemy.task import SqlAlchemyAgentTaskRepository
from app.repositories.task import AgentTaskVersionConflictError

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_agent_task_round_trip_and_optimistic_update(
    test_session: AsyncSession,
) -> None:
    repository = SqlAlchemyAgentTaskRepository(test_session)
    task = AgentTask(
        id=uuid4(),
        instruction="Купить билеты на концерт",
        target_url="https://tickets.example.com/event/42",
        person_ids=(uuid4(),),
        status=TaskStatus.READY,
        created_at=datetime(2026, 8, 13, 12, tzinfo=UTC),
    )

    created = await repository.create(task)
    loaded = await repository.get(task.id)
    paused = await repository.update(
        task.model_copy(update={"status": TaskStatus.PAUSED}),
        expected_version=0,
    )

    assert created == task
    assert loaded == task
    assert paused is not None
    assert paused.status is TaskStatus.PAUSED
    assert paused.version == 1

    with pytest.raises(AgentTaskVersionConflictError):
        await repository.update(task, expected_version=0)

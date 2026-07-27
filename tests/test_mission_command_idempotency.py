import asyncio
from uuid import uuid4

import pytest

from app.services.mission_command_idempotency import (
    MissionCommandIdempotencyConflictError,
    MissionCommandInProgressError,
    MissionCommandType,
)
from app.storage.mission_command_idempotency import (
    InMemoryMissionCommandIdempotencyStore,
)


def test_completed_command_returns_saved_mission_result() -> None:
    async def scenario() -> None:
        store = InMemoryMissionCommandIdempotencyStore()
        mission_id = uuid4()

        assert await store.begin(
            key="run-1", mission_id=mission_id, command=MissionCommandType.RUN
        ) is None
        await store.complete(key="run-1", mission_id=mission_id)
        assert await store.begin(
            key="run-1", mission_id=mission_id, command=MissionCommandType.RUN
        ) == mission_id

    asyncio.run(scenario())


def test_conflicting_command_key_is_rejected() -> None:
    async def scenario() -> None:
        store = InMemoryMissionCommandIdempotencyStore()
        await store.begin(
            key="command-1", mission_id=uuid4(), command=MissionCommandType.RUN
        )
        with pytest.raises(MissionCommandIdempotencyConflictError):
            await store.begin(
                key="command-1",
                mission_id=uuid4(),
                command=MissionCommandType.CONFIRM,
            )

    asyncio.run(scenario())


def test_in_progress_command_is_not_executed_twice() -> None:
    async def scenario() -> None:
        store = InMemoryMissionCommandIdempotencyStore()
        mission_id = uuid4()
        await store.begin(
            key="run-1", mission_id=mission_id, command=MissionCommandType.RUN
        )
        with pytest.raises(MissionCommandInProgressError):
            await store.begin(
                key="run-1", mission_id=mission_id, command=MissionCommandType.RUN
            )

    asyncio.run(scenario())

import asyncio
from uuid import UUID

from app.services.mission_command_idempotency import (
    MissionCommandIdempotencyConflictError,
    MissionCommandInProgressError,
    MissionCommandType,
)


class InMemoryMissionCommandIdempotencyStore:
    def __init__(self) -> None:
        self._records: dict[str, tuple[UUID, MissionCommandType, UUID | None]] = {}
        self._lock = asyncio.Lock()

    async def begin(
        self,
        *,
        key: str,
        mission_id: UUID,
        command: MissionCommandType,
    ) -> UUID | None:
        async with self._lock:
            receipt = self._records.get(key)
            if receipt is None:
                self._records[key] = (mission_id, command, None)
                return None
            stored_mission, stored_command, result = receipt
            if (stored_mission, stored_command) != (mission_id, command):
                raise MissionCommandIdempotencyConflictError
            if result is None:
                raise MissionCommandInProgressError
            return result

    async def complete(self, *, key: str, mission_id: UUID) -> None:
        async with self._lock:
            stored_mission, command, _ = self._records[key]
            self._records[key] = (stored_mission, command, mission_id)

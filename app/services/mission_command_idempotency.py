from enum import StrEnum
from typing import Protocol
from uuid import UUID


class MissionCommandType(StrEnum):
    RUN = "run"
    CONFIRM = "confirm"
    CANCEL = "cancel"


class MissionCommandIdempotencyConflictError(Exception):
    pass


class MissionCommandInProgressError(Exception):
    pass


class MissionCommandIdempotencyStore(Protocol):
    async def begin(
        self,
        *,
        key: str,
        mission_id: UUID,
        command: MissionCommandType,
    ) -> UUID | None:
        ...

    async def complete(self, *, key: str, mission_id: UUID) -> None:
        ...

    async def abort(
        self,
        *,
        key: str,
        mission_id: UUID,
        command: MissionCommandType,
    ) -> None:
        ...

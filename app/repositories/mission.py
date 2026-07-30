from __future__ import annotations

import builtins
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from app.domain.execution_attempt import MissionExecutionAttempt
from app.domain.mission import Mission


class RepositoryEntityNotFoundError(Exception):
    pass


class InvalidRepositoryTimeError(ValueError):
    pass


class MissionRepository(Protocol):
    async def create(self, mission: Mission) -> Mission:
        ...

    async def list(self) -> builtins.list[Mission]:
        ...

    async def list_due(
        self,
        current_time: datetime,
        limit: int = 100,
    ) -> builtins.list[Mission]:
        ...

    async def claim_due(
        self,
        current_time: datetime,
        limit: int = 100,
    ) -> builtins.list[Mission]:
        ...

    async def expire_due(
        self,
        current_time: datetime,
        limit: int = 100,
    ) -> builtins.list[Mission]:
        ...

    async def list_stale_processing(
        self,
        current_time: datetime,
        claim_timeout: timedelta,
        limit: int = 100,
    ) -> builtins.list[Mission]:
        ...

    async def recover_stale_processing(
        self,
        current_time: datetime,
        claim_timeout: timedelta,
        limit: int = 100,
    ) -> builtins.list[Mission]:
        ...

    async def list_execution_attempts(
        self,
        mission_id: UUID,
    ) -> builtins.list[MissionExecutionAttempt]:
        ...

    async def get(self, mission_id: UUID) -> Mission | None:
        ...

    async def exists(self, mission_id: UUID) -> bool:
        ...

    async def update(self, mission: Mission) -> Mission:
        ...

    async def clear(self) -> None:
        ...

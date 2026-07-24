from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from app.domain.mission import Mission


class RepositoryEntityNotFoundError(Exception):
    pass


class InvalidRepositoryTimeError(ValueError):
    pass


class MissionRepository(Protocol):
    async def create(self, mission: Mission) -> Mission:
        ...

    async def list(self) -> list[Mission]:
        ...

    async def list_due(
        self,
        current_time: datetime,
        limit: int = 100,
    ) -> list[Mission]:
        ...

    async def claim_due(
        self,
        current_time: datetime,
        limit: int = 100,
    ) -> list[Mission]:
        ...

    async def list_stale_processing(
        self,
        current_time: datetime,
        claim_timeout: timedelta,
        limit: int = 100,
    ) -> list[Mission]:
        ...

    async def recover_stale_processing(
        self,
        current_time: datetime,
        claim_timeout: timedelta,
        limit: int = 100,
    ) -> list[Mission]:
        ...

    async def get(self, mission_id: UUID) -> Mission | None:
        ...

    async def exists(self, mission_id: UUID) -> bool:
        ...

    async def update(self, mission: Mission) -> Mission:
        ...

    async def clear(self) -> None:
        ...

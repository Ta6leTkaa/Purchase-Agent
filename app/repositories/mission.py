from __future__ import annotations

import builtins
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from app.domain.execution_attempt import MissionExecutionAttempt
from app.domain.mission import Mission, MissionStatus, MissionSummary, MissionType
from app.services.mission_pagination import MissionCursor


class RepositoryEntityNotFoundError(Exception):
    pass


class InvalidRepositoryTimeError(ValueError):
    pass


class MissionRepository(Protocol):
    async def create(self, mission: Mission) -> Mission:
        ...

    async def list(
        self,
        *,
        status: MissionStatus | None = None,
        mission_type: MissionType | None = None,
        limit: int = 100,
    ) -> builtins.list[Mission]:
        ...

    async def list_summaries(
        self,
        *,
        status: MissionStatus | None = None,
        mission_type: MissionType | None = None,
        limit: int = 100,
    ) -> builtins.list[MissionSummary]:
        ...

    async def list_summary_page_candidates(
        self,
        *,
        status: MissionStatus | None = None,
        mission_type: MissionType | None = None,
        cursor: MissionCursor | None = None,
        limit: int = 101,
    ) -> builtins.list[MissionSummary]:
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

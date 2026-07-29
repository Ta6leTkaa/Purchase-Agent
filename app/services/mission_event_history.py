from contextlib import AbstractAsyncContextManager
from datetime import timedelta
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.execution import ExecutionEvent
from app.repositories.mission import MissionRepository
from app.services.mission_errors import MissionNotFoundError

DEFAULT_MISSION_EVENT_PAGE_SIZE = 50
MAX_MISSION_EVENT_PAGE_SIZE = 100
DEFAULT_MISSION_EVENT_WAIT_SECONDS = 0
MAX_MISSION_EVENT_WAIT_SECONDS = 30
MISSION_EVENT_POLL_INTERVAL = timedelta(milliseconds=500)


class MissionEventHistoryPageRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    after_sequence: int = Field(default=0, ge=0)
    limit: int = Field(
        default=DEFAULT_MISSION_EVENT_PAGE_SIZE,
        ge=1,
        le=MAX_MISSION_EVENT_PAGE_SIZE,
    )


class MissionEventHistoryPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    mission_id: UUID
    after_sequence: int
    latest_sequence: int
    has_more: bool
    items: tuple[ExecutionEvent, ...]


class MissionEventProjectionReader(Protocol):
    async def list_after(
        self,
        mission_id: UUID,
        after_sequence: int,
        fetch_limit: int,
    ) -> list[ExecutionEvent]:
        ...


class MissionEventReadRepositoryFactory(Protocol):
    def open(self) -> AbstractAsyncContextManager[MissionRepository]:
        ...


class MissionEventProjectionReaderFactory(Protocol):
    def open(self) -> AbstractAsyncContextManager[MissionEventProjectionReader]:
        ...


class AsyncWaiter(Protocol):
    def monotonic(self) -> float:
        ...

    async def sleep(self, duration: timedelta) -> None:
        ...


class GetMissionEventHistory:
    def __init__(
        self,
        mission_repository: MissionRepository,
        projection_reader: MissionEventProjectionReader | None = None,
    ) -> None:
        self._mission_repository = mission_repository
        self._projection_reader = projection_reader

    async def execute(
        self,
        mission_id: UUID,
        request: MissionEventHistoryPageRequest,
    ) -> MissionEventHistoryPage:
        mission = await self._mission_repository.get(mission_id)
        if mission is None:
            raise MissionNotFoundError

        if self._projection_reader is None:
            fetched_events = [
                event
                for event in mission.execution_log
                if event.sequence > request.after_sequence
            ][: request.limit + 1]
        else:
            fetched_events = await self._projection_reader.list_after(
                mission_id,
                request.after_sequence,
                request.limit + 1,
            )
        items = tuple(fetched_events[: request.limit])
        has_more = len(fetched_events) > request.limit
        latest_sequence = (
            items[-1].sequence if items else request.after_sequence
        )
        return MissionEventHistoryPage(
            mission_id=mission.id,
            after_sequence=request.after_sequence,
            latest_sequence=latest_sequence,
            has_more=has_more,
            items=items,
        )


class WaitForMissionEventHistory:
    """Reads bounded canonical event batches without holding a DB session asleep."""

    def __init__(
        self,
        mission_read_repository_factory: MissionEventReadRepositoryFactory,
        waiter: AsyncWaiter,
        projection_reader_factory: MissionEventProjectionReaderFactory | None = None,
        poll_interval: timedelta = MISSION_EVENT_POLL_INTERVAL,
    ) -> None:
        if poll_interval <= timedelta(0):
            raise ValueError("poll interval must be greater than zero")
        self._mission_read_repository_factory = mission_read_repository_factory
        self._waiter = waiter
        self._projection_reader_factory = projection_reader_factory
        self._poll_interval = poll_interval

    async def execute(
        self,
        mission_id: UUID,
        request: MissionEventHistoryPageRequest,
        wait_timeout: timedelta,
    ) -> MissionEventHistoryPage:
        async with self._mission_read_repository_factory.open() as repository:
            if not await repository.exists(mission_id):
                raise MissionNotFoundError
        result = await self._read_once(mission_id, request)
        if result.items or wait_timeout <= timedelta(0):
            return result
        deadline = self._waiter.monotonic() + wait_timeout.total_seconds()
        while True:
            remaining = deadline - self._waiter.monotonic()
            if remaining <= 0:
                return await self._read_once(mission_id, request)
            await self._waiter.sleep(
                min(self._poll_interval, timedelta(seconds=remaining))
            )
            result = await self._read_once(mission_id, request)
            if result.items or self._waiter.monotonic() >= deadline:
                return result

    async def _read_once(
        self,
        mission_id: UUID,
        request: MissionEventHistoryPageRequest,
    ) -> MissionEventHistoryPage:
        if self._projection_reader_factory is not None:
            async with self._projection_reader_factory.open() as reader:
                events = await reader.list_after(
                    mission_id,
                    request.after_sequence,
                    request.limit + 1,
                )
            return _page_from_events(mission_id, request, events)
        async with self._mission_read_repository_factory.open() as repository:
            mission = await repository.get(mission_id)
        if mission is None:
            raise MissionNotFoundError
        events = [
            event
            for event in mission.execution_log
            if event.sequence > request.after_sequence
        ][: request.limit + 1]
        return _page_from_events(mission.id, request, events)


def _page_from_events(
    mission_id: UUID,
    request: MissionEventHistoryPageRequest,
    fetched_events: list[ExecutionEvent],
) -> MissionEventHistoryPage:
    items = tuple(fetched_events[: request.limit])
    return MissionEventHistoryPage(
        mission_id=mission_id,
        after_sequence=request.after_sequence,
        latest_sequence=(items[-1].sequence if items else request.after_sequence),
        has_more=len(fetched_events) > request.limit,
        items=items,
    )

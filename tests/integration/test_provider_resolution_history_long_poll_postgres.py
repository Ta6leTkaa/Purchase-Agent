from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Awaitable, Callable
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.domain.mission import Mission, MissionType, TrainConstraints
from app.domain.provider_resolution import ProviderResolutionFailureReason
from app.repositories.mission import MissionRepository
from app.repositories.sqlalchemy.mission import SqlAlchemyMissionRepository
from app.services.provider_resolution_history import (
    AsyncWaiter,
    GetMissionProviderResolutionIncrement,
    ProviderResolutionIncrementRequest,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

CURRENT_TIME = datetime(2026, 7, 24, 10, 0, tzinfo=timezone.utc)


class FreshPostgresMissionReadFactory:
    def __init__(
        self,
        session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_maker = session_maker
        self.reads = 0

    @asynccontextmanager
    async def open(self) -> AsyncIterator[MissionRepository]:
        async with self._session_maker() as session:
            try:
                self.reads += 1
                yield SqlAlchemyMissionRepository(session)
            finally:
                await session.rollback()


class WriterWaiter(AsyncWaiter):
    def __init__(
        self,
        on_first_sleep: Callable[[], Awaitable[None]],
    ) -> None:
        self._on_first_sleep = on_first_sleep
        self._now = 0.0
        self._has_written = False

    def monotonic(self) -> float:
        return self._now

    async def sleep(self, delay: timedelta) -> None:
        self._now += delay.total_seconds()
        if not self._has_written:
            self._has_written = True
            await self._on_first_sleep()


def make_mission() -> Mission:
    return Mission(
        id=uuid4(),
        type=MissionType.TRAIN_TICKET,
        title="Moscow to Saint Petersburg",
        participant_ids=[uuid4()],
        provider="mock_train",
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Saint Petersburg",
            travel_date=date(2026, 8, 1),
            passengers_count=1,
        ),
    )


async def test_long_poll_reads_provider_event_committed_by_fresh_session(
    test_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    session_maker = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    mission = make_mission()
    repository = SqlAlchemyMissionRepository(test_session)
    await repository.create(mission)
    await test_session.commit()

    async def commit_provider_event() -> None:
        async with session_maker() as session:
            writer_repository = SqlAlchemyMissionRepository(session)
            persisted_mission = await writer_repository.get(mission.id)
            assert persisted_mission is not None
            persisted_mission.record_event(
                timestamp=CURRENT_TIME,
                event_type="provider_resolution_failed",
                message="No provider supports this mission type.",
                metadata={
                    "reason": (
                        ProviderResolutionFailureReason.no_supporting_provider.value
                    ),
                    "mission_type": MissionType.TRAIN_TICKET.value,
                    "candidate_provider_ids": [],
                },
            )
            await writer_repository.update(persisted_mission)
            await session.commit()

    read_factory = FreshPostgresMissionReadFactory(session_maker)
    increment = await GetMissionProviderResolutionIncrement(
        read_factory,
        WriterWaiter(commit_provider_event),
        poll_interval=timedelta(milliseconds=1),
    ).execute(
        mission.id,
        ProviderResolutionIncrementRequest(
            since_sequence=0,
            wait_timeout=timedelta(seconds=1),
        ),
    )

    assert read_factory.reads == 2
    assert [item.sequence for item in increment.items] == [1]
    assert increment.has_more is False
    assert [item.event_type.value for item in increment.items] == [
        "provider_resolution_failed"
    ]

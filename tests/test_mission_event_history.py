import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.mission import Mission, MissionType, TrainConstraints
from app.services.mission_errors import MissionNotFoundError
from app.services.mission_event_history import (
    GetMissionEventHistory,
    MissionEventHistoryPageRequest,
    WaitForMissionEventHistory,
)
from app.storage.memory import InMemoryMissionRepository


def make_mission() -> Mission:
    mission = Mission(
        id=uuid4(),
        type=MissionType.TRAIN_TICKET,
        title="Event history",
        participant_ids=[uuid4()],
        provider="mock_train",
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Saint Petersburg",
            travel_date=date(2026, 8, 1),
            passengers_count=1,
        ),
    )
    timestamp = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
    for event_type in ("first", "second", "third"):
        mission.record_event(
            timestamp=timestamp,
            event_type=event_type,
            message=event_type,
        )
    return mission


def test_event_history_filters_after_sequence_and_bounds_the_page() -> None:
    repository = InMemoryMissionRepository()
    mission = make_mission()
    asyncio.run(repository.create(mission))

    page = asyncio.run(
        GetMissionEventHistory(repository).execute(
            mission.id,
            MissionEventHistoryPageRequest(after_sequence=1, limit=1),
        )
    )

    assert [event.sequence for event in page.items] == [2]
    assert page.latest_sequence == 2
    assert page.has_more is True
    assert [event.sequence for event in mission.execution_log] == [1, 2, 3]


def test_event_history_returns_empty_page_after_latest_sequence() -> None:
    repository = InMemoryMissionRepository()
    mission = make_mission()
    asyncio.run(repository.create(mission))

    page = asyncio.run(
        GetMissionEventHistory(repository).execute(
            mission.id,
            MissionEventHistoryPageRequest(after_sequence=3),
        )
    )

    assert page.items == ()
    assert page.latest_sequence == 3
    assert page.has_more is False


def test_event_history_rejects_unknown_mission() -> None:
    repository = InMemoryMissionRepository()

    with pytest.raises(MissionNotFoundError):
        asyncio.run(
            GetMissionEventHistory(repository).execute(
                uuid4(),
                MissionEventHistoryPageRequest(),
            )
        )


def test_long_poll_returns_event_committed_while_waiting() -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()
        mission = make_mission()
        mission.execution_log.clear()
        mission.last_event_sequence = 0
        await repository.create(mission)
        waiter = AdvancingWaiter(repository, mission)
        service = WaitForMissionEventHistory(StaticFactory(repository), waiter)

        page = await service.execute(
            mission.id,
            MissionEventHistoryPageRequest(),
            timedelta(seconds=2),
        )

        assert [event.sequence for event in page.items] == [1]
        assert waiter.sleeps == 1

    asyncio.run(scenario())


def test_long_poll_returns_empty_page_after_timeout() -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()
        mission = make_mission()
        await repository.create(mission)
        waiter = TimeoutWaiter()
        service = WaitForMissionEventHistory(StaticFactory(repository), waiter)

        page = await service.execute(
            mission.id,
            MissionEventHistoryPageRequest(after_sequence=3),
            timedelta(seconds=1),
        )

        assert page.items == ()
        assert page.latest_sequence == 3
        assert waiter.sleeps == 2

    asyncio.run(scenario())


class StaticFactory:
    def __init__(self, repository: InMemoryMissionRepository) -> None:
        self._repository = repository

    @asynccontextmanager
    async def open(self) -> AsyncIterator[InMemoryMissionRepository]:
        yield self._repository


class TimeoutWaiter:
    def __init__(self) -> None:
        self._now = 0.0
        self.sleeps = 0

    def monotonic(self) -> float:
        return self._now

    async def sleep(self, duration: timedelta) -> None:
        self.sleeps += 1
        self._now += duration.total_seconds()


class AdvancingWaiter(TimeoutWaiter):
    def __init__(
        self,
        repository: InMemoryMissionRepository,
        mission: Mission,
    ) -> None:
        super().__init__()
        self._repository = repository
        self._mission = mission

    async def sleep(self, duration: timedelta) -> None:
        await super().sleep(duration)
        if self.sleeps == 1:
            self._mission.record_event(
                timestamp=datetime(2026, 7, 29, 12, 1, tzinfo=UTC),
                event_type="arrived",
                message="Arrived.",
            )
            await self._repository.update(self._mission)

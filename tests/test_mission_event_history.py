import asyncio
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from app.domain.mission import Mission, MissionType, TrainConstraints
from app.services.mission_errors import MissionNotFoundError
from app.services.mission_event_history import (
    GetMissionEventHistory,
    MissionEventHistoryPageRequest,
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

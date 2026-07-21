import asyncio
from datetime import date
from uuid import uuid4

from app.domain.mission import (
    Mission,
    MissionStatus,
    MissionType,
    TrainConstraints,
)
from app.storage.memory import InMemoryMissionRepository


def test_in_memory_repository_preserves_provider_id() -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()
        mission = Mission(
            id=uuid4(),
            type=MissionType.train_trip,
            title="Moscow to Saint Petersburg",
            participant_ids=[uuid4()],
            provider="mock_train",
            provider_id="mock_train",
            constraints=TrainConstraints(
                from_city="Moscow",
                to_city="Saint Petersburg",
                travel_date=date(2026, 8, 1),
                passengers_count=1,
            ),
        )

        await repository.create(mission)
        mission.status = MissionStatus.waiting
        await repository.update(mission)
        loaded_mission = await repository.get(mission.id)

        assert loaded_mission is not None
        assert loaded_mission.provider_id == "mock_train"

    asyncio.run(scenario())

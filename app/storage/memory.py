from datetime import datetime
from uuid import UUID

from app.domain.identity import Identity
from app.domain.mission import Mission, MissionStatus
from app.repositories.mission import InvalidRepositoryTimeError


class InMemoryIdentityRepository:
    def __init__(self) -> None:
        self._identities: dict[UUID, Identity] = {}

    async def create(self, identity: Identity) -> Identity:
        self._identities[identity.id] = identity
        return identity

    async def list(self) -> list[Identity]:
        return list(self._identities.values())

    async def get(self, identity_id: UUID) -> Identity | None:
        return self._identities.get(identity_id)

    async def clear(self) -> None:
        self._identities.clear()


class InMemoryMissionRepository:
    def __init__(self) -> None:
        self._missions: dict[UUID, Mission] = {}

    async def create(self, mission: Mission) -> Mission:
        self._missions[mission.id] = mission
        return mission

    async def list(self) -> list[Mission]:
        return list(self._missions.values())

    async def list_due(
        self,
        current_time: datetime,
        limit: int = 100,
    ) -> list[Mission]:
        _validate_list_due_arguments(current_time, limit)
        due_missions = [
            mission
            for mission in self._missions.values()
            if mission.status is MissionStatus.waiting
            and mission.scheduled_at is not None
            and mission.scheduled_at <= current_time
        ]
        return sorted(
            due_missions,
            key=lambda mission: mission.scheduled_at or current_time,
        )[:limit]

    async def get(self, mission_id: UUID) -> Mission | None:
        return self._missions.get(mission_id)

    async def update(self, mission: Mission) -> Mission:
        self._missions[mission.id] = mission
        return mission

    async def clear(self) -> None:
        self._missions.clear()


class MemoryStore:
    def __init__(self) -> None:
        self.identities = InMemoryIdentityRepository()
        self.missions = InMemoryMissionRepository()

    async def create_identity(self, identity: Identity) -> Identity:
        return await self.identities.create(identity)

    async def list_identities(self) -> list[Identity]:
        return await self.identities.list()

    async def get_identity(self, identity_id: UUID) -> Identity | None:
        return await self.identities.get(identity_id)

    async def create_mission(self, mission: Mission) -> Mission:
        return await self.missions.create(mission)

    async def list_missions(self) -> list[Mission]:
        return await self.missions.list()

    async def get_mission(self, mission_id: UUID) -> Mission | None:
        return await self.missions.get(mission_id)

    async def update_mission(self, mission: Mission) -> Mission:
        return await self.missions.update(mission)

    async def clear_identities(self) -> None:
        await self.identities.clear()

    def clear(self) -> None:
        self.identities._identities.clear()
        self.missions._missions.clear()


store = MemoryStore()


def _validate_list_due_arguments(
    current_time: datetime,
    limit: int,
) -> None:
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        raise InvalidRepositoryTimeError(
            "current_time must be timezone-aware"
        )
    if limit <= 0:
        raise ValueError("limit must be greater than 0")

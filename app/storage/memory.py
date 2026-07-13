from uuid import UUID

from app.domain.identity import Identity
from app.domain.mission import Mission


class InMemoryIdentityRepository:
    def __init__(self) -> None:
        self._identities: dict[UUID, Identity] = {}

    def create(self, identity: Identity) -> Identity:
        self._identities[identity.id] = identity
        return identity

    def list(self) -> list[Identity]:
        return list(self._identities.values())

    def get(self, identity_id: UUID) -> Identity | None:
        return self._identities.get(identity_id)

    def clear(self) -> None:
        self._identities.clear()


class InMemoryMissionRepository:
    def __init__(self) -> None:
        self._missions: dict[UUID, Mission] = {}

    def create(self, mission: Mission) -> Mission:
        self._missions[mission.id] = mission
        return mission

    def list(self) -> list[Mission]:
        return list(self._missions.values())

    def get(self, mission_id: UUID) -> Mission | None:
        return self._missions.get(mission_id)

    def update(self, mission: Mission) -> Mission:
        self._missions[mission.id] = mission
        return mission

    def clear(self) -> None:
        self._missions.clear()


class MemoryStore:
    def __init__(self) -> None:
        self.identities = InMemoryIdentityRepository()
        self.missions = InMemoryMissionRepository()

    def create_identity(self, identity: Identity) -> Identity:
        return self.identities.create(identity)

    def list_identities(self) -> list[Identity]:
        return self.identities.list()

    def get_identity(self, identity_id: UUID) -> Identity | None:
        return self.identities.get(identity_id)

    def create_mission(self, mission: Mission) -> Mission:
        return self.missions.create(mission)

    def list_missions(self) -> list[Mission]:
        return self.missions.list()

    def get_mission(self, mission_id: UUID) -> Mission | None:
        return self.missions.get(mission_id)

    def update_mission(self, mission: Mission) -> Mission:
        return self.missions.update(mission)

    def clear(self) -> None:
        self.identities.clear()
        self.missions.clear()


store = MemoryStore()

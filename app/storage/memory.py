from uuid import UUID

from app.domain.identity import Identity
from app.domain.mission import Mission


class MemoryStore:
    def __init__(self) -> None:
        self._identities: dict[UUID, Identity] = {}
        self._missions: dict[UUID, Mission] = {}

    def create_identity(self, identity: Identity) -> Identity:
        self._identities[identity.id] = identity
        return identity

    def list_identities(self) -> list[Identity]:
        return list(self._identities.values())

    def get_identity(self, identity_id: UUID) -> Identity | None:
        return self._identities.get(identity_id)

    def create_mission(self, mission: Mission) -> Mission:
        self._missions[mission.id] = mission
        return mission

    def list_missions(self) -> list[Mission]:
        return list(self._missions.values())

    def get_mission(self, mission_id: UUID) -> Mission | None:
        return self._missions.get(mission_id)

    def update_mission(self, mission: Mission) -> Mission:
        self._missions[mission.id] = mission
        return mission

    def clear(self) -> None:
        self._identities.clear()
        self._missions.clear()


store = MemoryStore()

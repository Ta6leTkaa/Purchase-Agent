from uuid import UUID

from app.domain.identity import Identity


class MemoryStore:
    def __init__(self) -> None:
        self._identities: dict[UUID, Identity] = {}

    def create_identity(self, identity: Identity) -> Identity:
        self._identities[identity.id] = identity
        return identity

    def list_identities(self) -> list[Identity]:
        return list(self._identities.values())

    def get_identity(self, identity_id: UUID) -> Identity | None:
        return self._identities.get(identity_id)

    def clear(self) -> None:
        self._identities.clear()


store = MemoryStore()

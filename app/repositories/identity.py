from typing import Protocol
from uuid import UUID

from app.domain.identity import Identity


class IdentityRepository(Protocol):
    def create(self, identity: Identity) -> Identity:
        ...

    def list(self) -> list[Identity]:
        ...

    def get(self, identity_id: UUID) -> Identity | None:
        ...

    def clear(self) -> None:
        ...

from typing import Protocol
from uuid import UUID

from app.domain.identity import Identity


class IdentityRepository(Protocol):
    async def create(self, identity: Identity) -> Identity:
        ...

    async def list(self) -> list[Identity]:
        ...

    async def get(self, identity_id: UUID) -> Identity | None:
        ...

    async def clear(self) -> None:
        ...

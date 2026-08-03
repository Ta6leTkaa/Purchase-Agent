import builtins
from typing import Protocol
from uuid import UUID

from app.domain.identity import Identity, IdentitySummary, Preferences
from app.services.identity_pagination import IdentityCursor


class IdentityRepository(Protocol):
    async def create(self, identity: Identity) -> Identity:
        ...

    async def list(
        self,
        *,
        query: str | None = None,
        limit: int = 100,
    ) -> builtins.list[Identity]:
        ...

    async def list_summaries(
        self,
        *,
        query: str | None = None,
        limit: int = 100,
    ) -> builtins.list[IdentitySummary]:
        ...

    async def list_summary_page_candidates(
        self,
        *,
        query: str | None = None,
        cursor: IdentityCursor | None = None,
        limit: int = 101,
    ) -> builtins.list[IdentitySummary]:
        ...

    async def get(self, identity_id: UUID) -> Identity | None:
        ...

    async def update_preferences(
        self,
        identity_id: UUID,
        preferences: "Preferences",
    ) -> Identity | None:
        ...

    async def update(self, identity: Identity) -> Identity | None:
        ...

    async def delete(self, identity_id: UUID) -> bool:
        ...

    async def clear(self) -> None:
        ...

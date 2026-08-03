import asyncio
from uuid import UUID

from app.services.resource_creation_idempotency import (
    ResourceCreationConflictError,
    ResourceCreationInProgressError,
    ResourceCreationScope,
)


class InMemoryResourceCreationIdempotencyStore:
    def __init__(self) -> None:
        self._records: dict[
            tuple[ResourceCreationScope, str],
            tuple[str, UUID | None],
        ] = {}
        self._lock = asyncio.Lock()

    async def begin(
        self,
        *,
        scope: ResourceCreationScope,
        key: str,
        fingerprint: str,
    ) -> UUID | None:
        async with self._lock:
            receipt = self._records.get((scope, key))
            if receipt is None:
                self._records[(scope, key)] = (fingerprint, None)
                return None
            stored_fingerprint, resource_id = receipt
            if stored_fingerprint != fingerprint:
                raise ResourceCreationConflictError
            if resource_id is None:
                raise ResourceCreationInProgressError
            return resource_id

    async def complete(
        self,
        *,
        scope: ResourceCreationScope,
        key: str,
        resource_id: UUID,
    ) -> None:
        async with self._lock:
            fingerprint, _ = self._records[(scope, key)]
            self._records[(scope, key)] = (fingerprint, resource_id)

    async def abort(
        self,
        *,
        scope: ResourceCreationScope,
        key: str,
        fingerprint: str,
    ) -> None:
        async with self._lock:
            if self._records.get((scope, key)) == (fingerprint, None):
                del self._records[(scope, key)]

    async def clear(self) -> None:
        async with self._lock:
            self._records.clear()

import hashlib
import json
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel


class ResourceCreationScope(StrEnum):
    IDENTITY = "identity"
    MISSION = "mission"


class ResourceCreationConflictError(Exception):
    pass


class ResourceCreationInProgressError(Exception):
    pass


class ResourceCreationIdempotencyStore(Protocol):
    async def begin(
        self,
        *,
        scope: ResourceCreationScope,
        key: str,
        fingerprint: str,
    ) -> UUID | None:
        ...

    async def complete(
        self,
        *,
        scope: ResourceCreationScope,
        key: str,
        resource_id: UUID,
    ) -> None:
        ...

    async def abort(
        self,
        *,
        scope: ResourceCreationScope,
        key: str,
        fingerprint: str,
    ) -> None:
        ...


def creation_fingerprint(request: BaseModel) -> str:
    canonical = json.dumps(
        request.model_dump(mode="json"),
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(canonical).hexdigest()

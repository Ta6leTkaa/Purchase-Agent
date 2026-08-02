from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import get_identity_repository
from app.domain.identity import Identity
from app.repositories.identity import IdentityRepository
from app.schemas.identity import IdentityCreate, IdentityPreferencesUpdate

router = APIRouter(prefix="/identities", tags=["identities"])
type IdentityRepositoryDep = Annotated[
    IdentityRepository,
    Depends(get_identity_repository),
]


@router.post("")
async def create_identity(
    identity: IdentityCreate,
    repository: IdentityRepositoryDep,
) -> Identity:
    return await repository.create(identity.to_domain())


@router.get("")
async def list_identities(
    repository: IdentityRepositoryDep,
    query: str | None = Query(default=None, alias="q", min_length=1, max_length=200),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[Identity]:
    normalized_query = query.strip() if query is not None else None
    if normalized_query == "":
        raise HTTPException(status_code=422, detail="q must not be blank")
    return await repository.list(query=normalized_query, limit=limit)


@router.get("/{identity_id}")
async def get_identity(
    identity_id: UUID,
    repository: IdentityRepositoryDep,
) -> Identity:
    identity = await repository.get(identity_id)
    if identity is None:
        raise HTTPException(status_code=404, detail="Identity not found")
    return identity


@router.put("/{identity_id}/preferences")
async def update_identity_preferences(
    identity_id: UUID,
    request: IdentityPreferencesUpdate,
    repository: IdentityRepositoryDep,
) -> Identity:
    identity = await repository.update_preferences(
        identity_id,
        request.preferences,
    )
    if identity is None:
        raise HTTPException(status_code=404, detail="Identity not found")
    return identity

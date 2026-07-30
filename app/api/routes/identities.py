from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

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
) -> list[Identity]:
    return await repository.list()


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

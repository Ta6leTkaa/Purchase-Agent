from typing import Annotated, TypeAlias
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_identity_repository
from app.domain.identity import Identity
from app.repositories.identity import IdentityRepository

router = APIRouter(prefix="/identities", tags=["identities"])
IdentityRepositoryDep: TypeAlias = Annotated[
    IdentityRepository,
    Depends(get_identity_repository),
]


@router.post("")
def create_identity(
    identity: Identity,
    repository: IdentityRepositoryDep,
) -> Identity:
    return repository.create(identity)


@router.get("")
def list_identities(
    repository: IdentityRepositoryDep,
) -> list[Identity]:
    return repository.list()


@router.get("/{identity_id}")
def get_identity(
    identity_id: UUID,
    repository: IdentityRepositoryDep,
) -> Identity:
    identity = repository.get(identity_id)
    if identity is None:
        raise HTTPException(status_code=404, detail="Identity not found")
    return identity

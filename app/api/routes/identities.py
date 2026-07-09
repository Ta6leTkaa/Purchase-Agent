from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.domain.identity import Identity
from app.storage.memory import store

router = APIRouter(prefix="/identities", tags=["identities"])


@router.post("")
def create_identity(identity: Identity) -> Identity:
    return store.create_identity(identity)


@router.get("")
def list_identities() -> list[Identity]:
    return store.list_identities()


@router.get("/{identity_id}")
def get_identity(identity_id: UUID) -> Identity:
    identity = store.get_identity(identity_id)
    if identity is None:
        raise HTTPException(status_code=404, detail="Identity not found")
    return identity

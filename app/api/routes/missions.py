from typing import Annotated, TypeAlias
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_identity_repository, get_mission_repository
from app.domain.mission import Mission
from app.repositories.identity import IdentityRepository
from app.repositories.mission import MissionRepository
from app.services.mission_engine import MissionNotFoundError, run_mission

router = APIRouter(prefix="/missions", tags=["missions"])
MissionRepositoryDep: TypeAlias = Annotated[
    MissionRepository,
    Depends(get_mission_repository),
]
IdentityRepositoryDep: TypeAlias = Annotated[
    IdentityRepository,
    Depends(get_identity_repository),
]


@router.post("")
async def create_mission(
    mission: Mission,
    repository: MissionRepositoryDep,
) -> Mission:
    return await repository.create(mission)


@router.get("")
async def list_missions(
    repository: MissionRepositoryDep,
) -> list[Mission]:
    return await repository.list()


@router.get("/{mission_id}")
async def get_mission(
    mission_id: UUID,
    repository: MissionRepositoryDep,
) -> Mission:
    mission = await repository.get(mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    return mission


@router.post("/{mission_id}/run")
async def run_mission_endpoint(
    mission_id: UUID,
    mission_repository: MissionRepositoryDep,
    identity_repository: IdentityRepositoryDep,
) -> Mission:
    try:
        return await run_mission(
            mission_id,
            mission_repository,
            identity_repository,
        )
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc

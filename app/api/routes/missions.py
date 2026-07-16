from typing import Annotated, TypeAlias
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_identity_repository, get_mission_repository
from app.domain.mission import Mission
from app.repositories.identity import IdentityRepository
from app.repositories.mission import MissionRepository
from app.schemas.mission import MissionCreate
from app.services.mission_engine import (
    InvalidMissionConfirmationError,
    InvalidMissionRunError,
    MissionNotReadyError,
    MissionNotFoundError,
    confirm_mission,
    run_mission,
)

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
    mission: MissionCreate,
    mission_repository: MissionRepositoryDep,
    identity_repository: IdentityRepositoryDep,
) -> Mission:
    unknown_participant_ids: list[str] = []
    for participant_id in mission.participant_ids:
        identity = await identity_repository.get(participant_id)
        if identity is None:
            unknown_participant_ids.append(str(participant_id))

    if unknown_participant_ids:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "unknown_participants",
                "message": "One or more participants do not exist",
                "participant_ids": unknown_participant_ids,
            },
        )

    return await mission_repository.create(mission.to_domain())


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
    except InvalidMissionRunError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MissionNotReadyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{mission_id}/confirm")
async def confirm_mission_endpoint(
    mission_id: UUID,
    mission_repository: MissionRepositoryDep,
) -> Mission:
    try:
        return await confirm_mission(mission_id, mission_repository)
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except InvalidMissionConfirmationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

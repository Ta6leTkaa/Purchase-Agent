from typing import Annotated, TypeAlias
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import (
    get_identity_repository,
    get_mission_repository,
    get_mission_provider_resolution_preview,
    get_provider_resolver,
    get_set_mission_provider,
)
from app.domain.mission import Mission
from app.repositories.identity import IdentityRepository
from app.repositories.mission import MissionRepository
from app.schemas.mission import (
    MissionCreate,
    MissionProviderResolutionPreviewResponse,
    SetMissionProviderRequest,
)
from app.services.mission_engine import (
    InvalidMissionConfirmationError,
    InvalidMissionRunError,
    MissionNotReadyError,
    confirm_mission,
    run_mission,
)
from app.services.mission_errors import MissionNotFoundError
from app.services.provider_resolver import ProviderResolver
from app.services.mission_provider_selection import (
    MissionProviderSelectionNotAllowedError,
    SetMissionProvider,
)
from app.services.provider_resolution_preview import (
    PreviewMissionProviderResolution,
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
ProviderResolverDep: TypeAlias = Annotated[
    ProviderResolver,
    Depends(get_provider_resolver),
]
SetMissionProviderDep: TypeAlias = Annotated[
    SetMissionProvider,
    Depends(get_set_mission_provider),
]
ProviderResolutionPreviewDep: TypeAlias = Annotated[
    PreviewMissionProviderResolution,
    Depends(get_mission_provider_resolution_preview),
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


@router.get(
    "/{mission_id}/provider-resolution",
    response_model=MissionProviderResolutionPreviewResponse,
    summary="Preview mission provider resolution",
    description=(
        "Returns the current provider-resolution outcome without executing or "
        "modifying the mission. The preview does not indicate that the mission "
        "is executable in its current lifecycle state."
    ),
    responses={
        404: {"description": "Mission not found"},
    },
)
async def preview_mission_provider_resolution_endpoint(
    mission_id: UUID,
    preview_mission_provider_resolution: ProviderResolutionPreviewDep,
) -> MissionProviderResolutionPreviewResponse:
    try:
        preview = await preview_mission_provider_resolution.execute(mission_id)
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    return MissionProviderResolutionPreviewResponse.model_validate(
        preview.model_dump()
    )


@router.put(
    "/{mission_id}/provider",
    summary="Set mission provider selection",
    description=(
        "Sets an explicit provider ID or clears it with null for automatic "
        "selection. The provider must support the mission type. This endpoint "
        "does not execute the mission."
    ),
    responses={
        404: {"description": "Mission not found"},
        409: {"description": "Mission state does not allow the update"},
        422: {"description": "Invalid, unknown, or incompatible provider"},
    },
)
async def set_mission_provider_endpoint(
    mission_id: UUID,
    request: SetMissionProviderRequest,
    set_mission_provider: SetMissionProviderDep,
) -> Mission:
    try:
        return await set_mission_provider.execute(
            mission_id,
            request.provider_id,
        )
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except MissionProviderSelectionNotAllowedError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "mission_provider_selection_not_allowed",
                "message": (
                    "Provider selection cannot be changed in the current "
                    "mission state."
                ),
                "details": {"status": exc.status.value},
            },
        ) from exc


@router.post("/{mission_id}/run")
async def run_mission_endpoint(
    mission_id: UUID,
    mission_repository: MissionRepositoryDep,
    identity_repository: IdentityRepositoryDep,
    provider_resolver: ProviderResolverDep,
) -> Mission:
    try:
        return await run_mission(
            mission_id,
            mission_repository,
            identity_repository,
            provider_resolver,
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

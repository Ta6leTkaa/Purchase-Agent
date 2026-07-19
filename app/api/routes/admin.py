from datetime import datetime, timedelta
from typing import Annotated, TypeAlias

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies.auth import require_admin_api_key
from app.cli import StaleMissionRecoveryResult
from app.dependencies import (
    get_current_time,
    get_identity_repository,
    get_mission_repository,
)
from app.repositories.identity import IdentityRepository
from app.repositories.mission import MissionRepository
from app.services.due_mission_processor import (
    DueMissionProcessingResult,
    process_due_missions,
)

router = APIRouter(prefix="/admin", tags=["admin"])
MissionRepositoryDep: TypeAlias = Annotated[
    MissionRepository,
    Depends(get_mission_repository),
]
IdentityRepositoryDep: TypeAlias = Annotated[
    IdentityRepository,
    Depends(get_identity_repository),
]
CurrentTimeDep: TypeAlias = Annotated[datetime, Depends(get_current_time)]
AdminApiKeyDep: TypeAlias = Annotated[None, Depends(require_admin_api_key)]


class ProcessDueMissionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=100, ge=1, le=500)


class RecoverStaleMissionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_timeout_seconds: int = Field(
        default=900,
        ge=1,
        le=86400,
        description="Maximum acceptable claim age in seconds.",
    )
    limit: int = Field(
        default=100,
        ge=1,
        le=500,
        description="Maximum number of stale missions to recover.",
    )


@router.post("/missions/process-due")
async def process_due_missions_endpoint(
    request: ProcessDueMissionsRequest,
    _admin_api_key: AdminApiKeyDep,
    mission_repository: MissionRepositoryDep,
    identity_repository: IdentityRepositoryDep,
    current_time: CurrentTimeDep,
) -> DueMissionProcessingResult:
    """Run one local-development admin cycle for due mission processing."""
    return await process_due_missions(
        mission_repository,
        identity_repository,
        current_time,
        limit=request.limit,
    )


@router.post(
    "/missions/recover-stale",
    response_model=StaleMissionRecoveryResult,
    summary="Recover stale processing missions",
)
async def recover_stale_missions_endpoint(
    request: RecoverStaleMissionsRequest,
    _admin_api_key: AdminApiKeyDep,
    mission_repository: MissionRepositoryDep,
    current_time: CurrentTimeDep,
) -> StaleMissionRecoveryResult:
    """Return stale missions to waiting without starting their execution."""
    recovered_missions = await mission_repository.recover_stale_processing(
        current_time=current_time,
        claim_timeout=timedelta(seconds=request.claim_timeout_seconds),
        limit=request.limit,
    )
    return StaleMissionRecoveryResult(
        recovered_count=len(recovered_missions),
        recovered_mission_ids=[mission.id for mission in recovered_missions],
    )

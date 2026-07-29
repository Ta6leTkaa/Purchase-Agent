from datetime import datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies.auth import require_admin_api_key
from app.cli import StaleMissionRecoveryResult
from app.dependencies import (
    get_current_time,
    get_identity_repository,
    get_mission_event_projection_verifier,
    get_mission_repository,
    get_provider_history_projection_verifier,
    get_provider_resolver,
)
from app.repositories.identity import IdentityRepository
from app.repositories.mission import MissionRepository
from app.services.due_mission_processor import (
    DueMissionProcessingResult,
    process_due_missions,
)
from app.services.mission_errors import MissionNotFoundError
from app.services.mission_event_projection import (
    MissionEventProjectionVerification,
    VerifyMissionEventProjection,
)
from app.services.provider_history_verification import (
    MissionProviderHistoryProjectionVerification,
    VerifyMissionProviderHistoryProjection,
)
from app.services.provider_resolver import ProviderResolver

router = APIRouter(prefix="/admin", tags=["admin"])
type MissionRepositoryDep = Annotated[
    MissionRepository,
    Depends(get_mission_repository),
]
type IdentityRepositoryDep = Annotated[
    IdentityRepository,
    Depends(get_identity_repository),
]
type ProviderResolverDep = Annotated[
    ProviderResolver,
    Depends(get_provider_resolver),
]
type CurrentTimeDep = Annotated[datetime, Depends(get_current_time)]
type AdminApiKeyDep = Annotated[None, Depends(require_admin_api_key)]
type ProviderHistoryVerifierDep = Annotated[
    VerifyMissionProviderHistoryProjection,
    Depends(get_provider_history_projection_verifier),
]
type MissionEventProjectionVerifierDep = Annotated[
    VerifyMissionEventProjection,
    Depends(get_mission_event_projection_verifier),
]


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
    provider_resolver: ProviderResolverDep,
    current_time: CurrentTimeDep,
) -> DueMissionProcessingResult:
    """Run one local-development admin cycle for due mission processing."""
    return await process_due_missions(
        mission_repository,
        identity_repository,
        current_time,
        limit=request.limit,
        provider_resolver=provider_resolver,
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


@router.get(
    "/missions/{mission_id}/provider-history-projection/verification",
    response_model=MissionProviderHistoryProjectionVerification,
    summary="Verify provider history projection consistency",
)
async def verify_provider_history_projection_endpoint(
    mission_id: UUID,
    _admin_api_key: AdminApiKeyDep,
    verifier: ProviderHistoryVerifierDep,
) -> MissionProviderHistoryProjectionVerification:
    try:
        return await verifier.execute(mission_id)
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc


@router.get(
    "/missions/{mission_id}/event-projection/verification",
    response_model=MissionEventProjectionVerification,
    summary="Verify Mission event projection consistency",
)
async def verify_mission_event_projection_endpoint(
    mission_id: UUID,
    _admin_api_key: AdminApiKeyDep,
    verifier: MissionEventProjectionVerifierDep,
) -> MissionEventProjectionVerification:
    """Compare the canonical event log with its relational read projection."""
    try:
        return await verifier.execute(mission_id)
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc

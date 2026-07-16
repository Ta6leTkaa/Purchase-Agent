from datetime import datetime
from typing import Annotated, TypeAlias

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

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


class ProcessDueMissionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=100, ge=1, le=500)


@router.post("/missions/process-due")
async def process_due_missions_endpoint(
    request: ProcessDueMissionsRequest,
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

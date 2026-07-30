from datetime import datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import require_admin_api_key
from app.cli import StaleMissionRecoveryResult
from app.dependencies import (
    get_current_time,
    get_identity_repository,
    get_mission_event_projection_verifier,
    get_mission_repository,
    get_provider_history_projection_verifier,
    get_provider_resolver,
    get_storage_session,
)
from app.domain.notification import (
    NotificationOutboxMessage,
    NotificationOutboxStatistics,
    NotificationOutboxStatus,
)
from app.repositories.identity import IdentityRepository
from app.repositories.mission import MissionRepository
from app.repositories.sqlalchemy.notification_outbox import (
    SqlAlchemyNotificationOutboxRepository,
)
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
type StorageSessionDep = Annotated[AsyncSession | None, Depends(get_storage_session)]


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


class RecoverStaleNotificationsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_timeout_seconds: int = Field(
        default=300,
        ge=1,
        le=86400,
        description="Maximum acceptable notification claim age in seconds.",
    )
    limit: int = Field(
        default=100,
        ge=1,
        le=500,
        description="Maximum number of stale notification claims to recover.",
    )


class StaleNotificationRecoveryResult(BaseModel):
    recovered_count: int
    recovered_message_ids: list[UUID]


class NotificationOutboxMessageSummary(BaseModel):
    """Delivery metadata deliberately excluding the potentially sensitive payload."""

    id: UUID
    mission_id: UUID
    event_id: UUID
    event_type: str
    occurred_at: datetime
    status: NotificationOutboxStatus
    delivery_attempts: int
    available_at: datetime
    claimed_at: datetime | None
    delivered_at: datetime | None
    last_error: str | None

    @classmethod
    def from_message(
        cls, message: NotificationOutboxMessage
    ) -> "NotificationOutboxMessageSummary":
        return cls.model_validate(message, from_attributes=True)


def _notification_outbox_repository(
    session: AsyncSession | None,
) -> SqlAlchemyNotificationOutboxRepository:
    if session is None:
        raise HTTPException(
            status_code=503,
            detail="Notification outbox requires the database storage backend",
        )
    return SqlAlchemyNotificationOutboxRepository(session)


@router.get(
    "/notification-outbox/statistics",
    response_model=NotificationOutboxStatistics,
    summary="Summarize notification delivery backlog",
)
async def notification_outbox_statistics_endpoint(
    _admin_api_key: AdminApiKeyDep,
    session: StorageSessionDep,
    current_time: CurrentTimeDep,
) -> NotificationOutboxStatistics:
    """Expose bounded delivery health data without notification payloads."""
    return await _notification_outbox_repository(session).get_statistics(
        current_time
    )


@router.post(
    "/notification-outbox/recover-stale",
    response_model=StaleNotificationRecoveryResult,
    summary="Recover stale notification delivery claims",
)
async def recover_stale_notifications_endpoint(
    request: RecoverStaleNotificationsRequest,
    _admin_api_key: AdminApiKeyDep,
    session: StorageSessionDep,
    current_time: CurrentTimeDep,
) -> StaleNotificationRecoveryResult:
    """Return abandoned processing messages to the pending delivery queue."""
    recovered = await _notification_outbox_repository(
        session
    ).recover_stale_claims(
        current_time,
        timedelta(seconds=request.claim_timeout_seconds),
        request.limit,
    )
    return StaleNotificationRecoveryResult(
        recovered_count=len(recovered),
        recovered_message_ids=[message.id for message in recovered],
    )


@router.get(
    "/notification-outbox",
    response_model=list[NotificationOutboxMessageSummary],
    summary="List notification delivery outbox",
)
async def list_notification_outbox_endpoint(
    _admin_api_key: AdminApiKeyDep,
    session: StorageSessionDep,
    status: NotificationOutboxStatus | None = None,
    mission_id: UUID | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[NotificationOutboxMessageSummary]:
    """Inspect delivery state without exposing notification payloads."""
    messages = await _notification_outbox_repository(session).list_messages(
        status=status.value if status is not None else None,
        mission_id=mission_id,
        limit=limit,
    )
    return [NotificationOutboxMessageSummary.from_message(item) for item in messages]


@router.post(
    "/notification-outbox/{message_id}/requeue",
    response_model=NotificationOutboxMessageSummary,
    summary="Requeue a failed notification",
)
async def requeue_failed_notification_endpoint(
    message_id: UUID,
    _admin_api_key: AdminApiKeyDep,
    session: StorageSessionDep,
    current_time: CurrentTimeDep,
) -> NotificationOutboxMessageSummary:
    """Give a dead-lettered delivery a new five-attempt delivery budget."""
    repository = _notification_outbox_repository(session)
    existing = await repository.get_message(message_id)
    if existing is None:
        raise HTTPException(
            status_code=404,
            detail="Notification outbox message not found",
        )
    if existing.status is not NotificationOutboxStatus.failed:
        raise HTTPException(
            status_code=409,
            detail="Only failed notification outbox messages can be requeued",
        )
    message = await repository.requeue_failed(
        message_id,
        current_time,
    )
    if message is None:
        raise HTTPException(
            status_code=409,
            detail="Only failed notification outbox messages can be requeued",
        )
    return NotificationOutboxMessageSummary.from_message(message)


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

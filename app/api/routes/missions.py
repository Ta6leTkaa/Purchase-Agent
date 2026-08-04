from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Response
from pydantic import BaseModel

from app.adapters import ProviderRegistry
from app.api.dependencies.auth import require_api_key
from app.dependencies import (
    get_identity_repository,
    get_mission_command_idempotency_store,
    get_mission_event_projection_reader,
    get_mission_provider_resolution_history,
    get_mission_provider_resolution_increment,
    get_mission_provider_resolution_preview,
    get_mission_repository,
    get_provider_registry,
    get_provider_resolver,
    get_resource_creation_idempotency_store,
    get_set_mission_provider,
    get_wait_for_mission_event_history,
)
from app.domain.mission import Mission, MissionStatus, MissionSummary, MissionType
from app.repositories.identity import IdentityRepository
from app.repositories.mission import MissionRepository
from app.repositories.sqlalchemy.mission_event import (
    SqlAlchemyMissionEventProjectionRepository,
)
from app.schemas.mission import (
    MissionCreate,
    MissionEventHistoryResponse,
    MissionExecutionAttemptHistoryResponse,
    MissionProviderResolutionHistoryResponse,
    MissionProviderResolutionIncrementResponse,
    MissionProviderResolutionPreviewResponse,
    MissionUpdateRequest,
    RetryMissionRequest,
    ScheduleMissionRequest,
    SetMissionProviderRequest,
)
from app.services.http_preconditions import (
    PRIVATE_REVALIDATION_CACHE_CONTROL,
    if_none_match_matches,
)
from app.services.mission_command_idempotency import (
    MissionCommandIdempotencyConflictError,
    MissionCommandIdempotencyStore,
    MissionCommandInProgressError,
    MissionCommandType,
)
from app.services.mission_engine import (
    InvalidMissionCancellationError,
    InvalidMissionConfirmationError,
    InvalidMissionRunError,
    MissionNotReadyError,
    cancel_mission,
    confirm_mission,
    run_mission,
)
from app.services.mission_errors import MissionNotFoundError
from app.services.mission_event_history import (
    DEFAULT_MISSION_EVENT_PAGE_SIZE,
    DEFAULT_MISSION_EVENT_WAIT_SECONDS,
    MAX_MISSION_EVENT_PAGE_SIZE,
    MAX_MISSION_EVENT_WAIT_SECONDS,
    GetMissionEventHistory,
    MissionEventHistoryPageRequest,
    WaitForMissionEventHistory,
)
from app.services.mission_outcome import MissionOutcome, get_mission_outcome
from app.services.mission_pagination import (
    InvalidMissionCursorError,
    MissionCursor,
    MissionCursorCodec,
)
from app.services.mission_pause import (
    MissionPauseNotAllowedError,
    MissionResumeNotAllowedError,
    pause_mission,
    resume_mission,
)
from app.services.mission_provider_selection import (
    MissionProviderSelectionNotAllowedError,
    SetMissionProvider,
)
from app.services.mission_retry import (
    InvalidMissionRetryTimeError,
    MissionAttemptsExhaustedError,
    MissionRetryNotAllowedError,
    retry_mission,
)
from app.services.mission_scheduling import (
    InvalidMissionScheduleError,
    MissionSchedulingNotAllowedError,
    schedule_mission,
)
from app.services.mission_update import (
    InvalidMissionUpdateError,
    MissionUpdateNotAllowedError,
    update_mission,
)
from app.services.provider_resolution_history import (
    DEFAULT_PROVIDER_HISTORY_INCREMENT_LIMIT,
    DEFAULT_PROVIDER_HISTORY_PAGE_SIZE,
    MAX_PROVIDER_HISTORY_INCREMENT_LIMIT,
    MAX_PROVIDER_HISTORY_PAGE_SIZE,
    MAX_PROVIDER_HISTORY_WAIT_SECONDS,
    GetMissionProviderResolutionHistory,
    GetMissionProviderResolutionIncrement,
    InvalidProviderHistoryCursorError,
    ProviderHistoryCursorCodec,
    ProviderResolutionHistoryPageRequest,
    ProviderResolutionIncrementRequest,
)
from app.services.provider_resolution_preview import (
    PreviewMissionProviderResolution,
)
from app.services.provider_resolver import ProviderResolver
from app.services.resource_creation_idempotency import (
    ResourceCreationConflictError,
    ResourceCreationIdempotencyStore,
    ResourceCreationInProgressError,
    ResourceCreationScope,
    creation_fingerprint,
)


class MissionSummaryPage(BaseModel):
    items: list[MissionSummary]
    has_more: bool
    next_cursor: str | None

router = APIRouter(
    prefix="/missions",
    tags=["missions"],
    dependencies=[Depends(require_api_key)],
)
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
type ProviderRegistryDep = Annotated[
    ProviderRegistry,
    Depends(get_provider_registry),
]
type SetMissionProviderDep = Annotated[
    SetMissionProvider,
    Depends(get_set_mission_provider),
]
type ProviderResolutionPreviewDep = Annotated[
    PreviewMissionProviderResolution,
    Depends(get_mission_provider_resolution_preview),
]
type ProviderResolutionHistoryDep = Annotated[
    GetMissionProviderResolutionHistory,
    Depends(get_mission_provider_resolution_history),
]
type ProviderResolutionIncrementDep = Annotated[
    GetMissionProviderResolutionIncrement,
    Depends(get_mission_provider_resolution_increment),
]
type MissionCommandIdempotencyStoreDep = Annotated[
    MissionCommandIdempotencyStore,
    Depends(get_mission_command_idempotency_store),
]
type ResourceCreationIdempotencyStoreDep = Annotated[
    ResourceCreationIdempotencyStore,
    Depends(get_resource_creation_idempotency_store),
]
type MissionEventProjectionReaderDep = Annotated[
    SqlAlchemyMissionEventProjectionRepository | None,
    Depends(get_mission_event_projection_reader),
]
type WaitForMissionEventHistoryDep = Annotated[
    WaitForMissionEventHistory,
    Depends(get_wait_for_mission_event_history),
]


@router.post("")
async def create_mission(
    mission: MissionCreate,
    mission_repository: MissionRepositoryDep,
    identity_repository: IdentityRepositoryDep,
    idempotency_store: ResourceCreationIdempotencyStoreDep,
    response: Response,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=255),
    ] = None,
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

    if idempotency_key is None:
        created = await mission_repository.create(mission.to_domain())
        _set_mission_etag(response, created)
        return created
    fingerprint = creation_fingerprint(mission)
    try:
        previous_id = await idempotency_store.begin(
            scope=ResourceCreationScope.MISSION,
            key=idempotency_key,
            fingerprint=fingerprint,
        )
        if previous_id is not None:
            previous = await mission_repository.get(previous_id)
            assert previous is not None
            _set_mission_etag(response, previous)
            return previous
        created = await mission_repository.create(mission.to_domain())
        await idempotency_store.complete(
            scope=ResourceCreationScope.MISSION,
            key=idempotency_key,
            resource_id=created.id,
        )
    except ResourceCreationConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "idempotency_key_conflict",
                "message": (
                    "Idempotency-Key was already used with another request."
                ),
            },
        ) from exc
    except ResourceCreationInProgressError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "idempotent_request_in_progress",
                "message": "A request with this Idempotency-Key is in progress.",
            },
        ) from exc
    except BaseException:
        await idempotency_store.abort(
            scope=ResourceCreationScope.MISSION,
            key=idempotency_key,
            fingerprint=fingerprint,
        )
        raise
    _set_mission_etag(response, created)
    return created


@router.get("")
async def list_missions(
    repository: MissionRepositoryDep,
    status: MissionStatus | None = None,
    mission_type: Annotated[
        MissionType | None,
        Query(alias="type"),
    ] = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[Mission]:
    return await repository.list(
        status=status,
        mission_type=mission_type,
        limit=limit,
    )


@router.get("/summaries")
async def list_mission_summaries(
    repository: MissionRepositoryDep,
    status: MissionStatus | None = None,
    mission_type: Annotated[
        MissionType | None,
        Query(alias="type"),
    ] = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[MissionSummary]:
    return await repository.list_summaries(
        status=status,
        mission_type=mission_type,
        limit=limit,
    )


@router.get("/summaries/page")
async def page_mission_summaries(
    repository: MissionRepositoryDep,
    status: MissionStatus | None = None,
    mission_type: Annotated[
        MissionType | None,
        Query(alias="type"),
    ] = None,
    limit: int = Query(default=100, ge=1, le=500),
    cursor: str | None = None,
) -> MissionSummaryPage:
    codec = MissionCursorCodec()
    try:
        decoded_cursor = codec.decode(cursor) if cursor is not None else None
    except InvalidMissionCursorError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_mission_cursor",
                "message": "Mission cursor is invalid.",
            },
        ) from exc
    candidates = await repository.list_summary_page_candidates(
        status=status,
        mission_type=mission_type,
        cursor=decoded_cursor,
        limit=limit + 1,
    )
    has_more = len(candidates) > limit
    items = candidates[:limit]
    next_cursor = None
    if has_more:
        next_cursor = codec.encode(MissionCursor(mission_id=items[-1].id))
    return MissionSummaryPage(
        items=items,
        has_more=has_more,
        next_cursor=next_cursor,
    )


@router.get(
    "/{mission_id}",
    response_model=Mission,
    responses={304: {"description": "Mission has not changed"}},
)
async def get_mission(
    mission_id: UUID,
    repository: MissionRepositoryDep,
    response: Response,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> Mission | Response:
    mission = await repository.get(mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    etag = _mission_etag(mission)
    if if_none_match_matches(if_none_match, etag):
        return Response(
            status_code=304,
            headers={
                "Cache-Control": PRIVATE_REVALIDATION_CACHE_CONTROL,
                "ETag": etag,
            },
        )
    response.headers["Cache-Control"] = PRIVATE_REVALIDATION_CACHE_CONTROL
    response.headers["ETag"] = etag
    return mission


@router.get(
    "/{mission_id}/outcome",
    response_model=MissionOutcome,
    summary="Get actionable mission outcome",
    responses={404: {"description": "Mission not found"}},
)
async def get_mission_outcome_endpoint(
    mission_id: UUID,
    repository: MissionRepositoryDep,
    response: Response,
) -> MissionOutcome:
    mission = await repository.get(mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    response.headers["Cache-Control"] = PRIVATE_REVALIDATION_CACHE_CONTROL
    _set_mission_etag(response, mission)
    return get_mission_outcome(mission)


@router.patch("/{mission_id}")
async def update_mission_endpoint(
    mission_id: UUID,
    request: MissionUpdateRequest,
    mission_repository: MissionRepositoryDep,
    provider_registry: ProviderRegistryDep,
    response: Response,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> Mission:
    try:
        await _ensure_mission_version(
            mission_repository,
            mission_id,
            if_match,
        )
        mission = await update_mission(
            mission_id,
            mission_repository,
            provider_registry,
            title=request.title,
            fallback_rules=request.fallback_rules,
            execution_mode=request.execution_mode,
            max_execution_attempts=request.max_execution_attempts,
        )
        _set_mission_etag(response, mission)
        return mission
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except MissionUpdateNotAllowedError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "mission_update_not_allowed",
                "message": str(exc),
                "details": {"status": exc.status.value},
            },
        ) from exc
    except InvalidMissionUpdateError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_mission_update",
                "message": str(exc),
            },
        ) from exc


@router.get(
    "/{mission_id}/execution-attempts",
    response_model=MissionExecutionAttemptHistoryResponse,
    summary="Get mission execution attempts",
    description=(
        "Returns the immutable audit records created by successful due-mission "
        "claims. This read-only endpoint does not execute, recover, or modify "
        "the mission."
    ),
    responses={
        404: {"description": "Mission not found"},
    },
)
async def get_mission_execution_attempts_endpoint(
    mission_id: UUID,
    repository: MissionRepositoryDep,
) -> MissionExecutionAttemptHistoryResponse:
    mission = await repository.get(mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    attempts = await repository.list_execution_attempts(mission_id)
    return MissionExecutionAttemptHistoryResponse.from_domain(
        mission_id,
        attempts,
    )


@router.get(
    "/{mission_id}/events",
    response_model=MissionEventHistoryResponse,
    summary="Get mission event history",
    description=(
        "Returns a bounded, read-only page of canonical Mission events after "
        "a sequence position. This endpoint does not execute or modify the "
        "mission."
    ),
    responses={404: {"description": "Mission not found"}},
)
async def get_mission_event_history_endpoint(
    mission_id: UUID,
    repository: MissionRepositoryDep,
    projection_reader: MissionEventProjectionReaderDep,
    wait_for_event_history: WaitForMissionEventHistoryDep,
    after_sequence: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[
        int,
        Query(ge=1, le=MAX_MISSION_EVENT_PAGE_SIZE),
    ] = DEFAULT_MISSION_EVENT_PAGE_SIZE,
    wait_seconds: Annotated[
        int,
        Query(
            ge=0,
            le=MAX_MISSION_EVENT_WAIT_SECONDS,
            description="Maximum seconds to wait for newly committed events.",
        ),
    ] = DEFAULT_MISSION_EVENT_WAIT_SECONDS,
) -> MissionEventHistoryResponse:
    try:
        request = MissionEventHistoryPageRequest(
            after_sequence=after_sequence,
            limit=limit,
        )
        page = (
            await wait_for_event_history.execute(
                mission_id,
                request,
                timedelta(seconds=wait_seconds),
            )
            if wait_seconds
            else await GetMissionEventHistory(repository, projection_reader).execute(
                mission_id,
                request,
            )
        )
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    return MissionEventHistoryResponse.from_application(page)


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


@router.get(
    "/{mission_id}/provider-resolution-history/since/{sequence}",
    response_model=MissionProviderResolutionIncrementResponse,
    summary="Get incremental mission provider resolution history",
    description=(
        "Returns provider selection and resolution events with a persisted "
        "sequence greater than the requested boundary. With a positive "
        "wait_seconds value, it waits for newly committed matching events. "
        "Results are returned as bounded sequence-based batches. "
        "This read-only endpoint does not resolve providers or execute the "
        "mission."
    ),
    responses={
        404: {"description": "Mission not found"},
        422: {"description": "Invalid sequence"},
    },
)
async def get_mission_provider_resolution_increment_endpoint(
    mission_id: UUID,
    sequence: Annotated[
        int,
        Path(
            ge=0,
            description=(
                "Return provider-related events with a persisted sequence "
                "strictly greater than this value."
            ),
        ),
    ],
    provider_resolution_increment: ProviderResolutionIncrementDep,
    response: Response,
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=MAX_PROVIDER_HISTORY_INCREMENT_LIMIT,
            description=(
                "Maximum number of provider-related events returned in this "
                "incremental batch."
            ),
        ),
    ] = DEFAULT_PROVIDER_HISTORY_INCREMENT_LIMIT,
    wait_seconds: Annotated[
        int,
        Query(
            ge=0,
            le=MAX_PROVIDER_HISTORY_WAIT_SECONDS,
            description=(
                "Maximum number of seconds to wait for new provider-related "
                "mission events. The endpoint reads immediately first and "
                "returns an empty response when the timeout expires."
            ),
        ),
    ] = 0,
) -> MissionProviderResolutionIncrementResponse:
    try:
        increment = await provider_resolution_increment.execute(
            mission_id,
            ProviderResolutionIncrementRequest(
                since_sequence=sequence,
                limit=limit,
                wait_timeout=timedelta(seconds=wait_seconds),
            ),
        )
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    response.headers["Cache-Control"] = "no-store"
    return MissionProviderResolutionIncrementResponse.from_application(
        increment
    )


@router.get(
    "/{mission_id}/provider-resolution-history",
    response_model=MissionProviderResolutionHistoryResponse,
    summary="Get mission provider resolution history",
    description=(
        "Returns chronological provider selection and resolution events using "
        "cursor pagination. This read-only endpoint does not resolve "
        "providers, inspect the current registry, or execute the mission."
    ),
    responses={
        404: {"description": "Mission not found"},
        422: {"description": "Invalid page limit or cursor"},
    },
)
async def get_mission_provider_resolution_history_endpoint(
    mission_id: UUID,
    provider_resolution_history: ProviderResolutionHistoryDep,
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=MAX_PROVIDER_HISTORY_PAGE_SIZE,
            description=(
                "Maximum number of provider-related history items returned "
                "in this page."
            ),
        ),
    ] = DEFAULT_PROVIDER_HISTORY_PAGE_SIZE,
    cursor: Annotated[
        str | None,
        Query(
            description=(
                "Opaque cursor from the previous history page. Events at or "
                "before this cursor are excluded."
            ),
        ),
    ] = None,
) -> MissionProviderResolutionHistoryResponse:
    try:
        decoded_cursor = (
            ProviderHistoryCursorCodec().decode(cursor)
            if cursor is not None
            else None
        )
        page_request = ProviderResolutionHistoryPageRequest(
            limit=limit,
            cursor=decoded_cursor,
        )
    except InvalidProviderHistoryCursorError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_cursor",
                "message": "Provider history cursor is invalid.",
            },
        ) from exc
    try:
        history = await provider_resolution_history.execute(
            mission_id,
            page_request,
        )
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except InvalidProviderHistoryCursorError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_cursor",
                "message": "Provider history cursor is invalid.",
            },
        ) from exc
    return MissionProviderResolutionHistoryResponse.from_application(history)


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
    mission_repository: MissionRepositoryDep,
    set_mission_provider: SetMissionProviderDep,
    response: Response,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> Mission:
    try:
        await _ensure_mission_version(mission_repository, mission_id, if_match)
        mission = await set_mission_provider.execute(
            mission_id,
            request.provider_id,
        )
        _set_mission_etag(response, mission)
        return mission
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


@router.put(
    "/{mission_id}/schedule",
    summary="Schedule mission",
    description=(
        "Schedules or reschedules a created or waiting Mission. This endpoint "
        "does not execute the mission."
    ),
    responses={
        404: {"description": "Mission not found"},
        409: {"description": "Mission state does not allow scheduling"},
    },
)
async def schedule_mission_endpoint(
    mission_id: UUID,
    request: ScheduleMissionRequest,
    mission_repository: MissionRepositoryDep,
    response: Response,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> Mission:
    try:
        await _ensure_mission_version(mission_repository, mission_id, if_match)
        mission = await schedule_mission(
            mission_id,
            request.scheduled_at,
            mission_repository,
        )
        _set_mission_etag(response, mission)
        return mission
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except MissionSchedulingNotAllowedError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "mission_scheduling_not_allowed",
                "message": "Mission scheduling cannot be changed in this state.",
                "details": {"status": exc.status.value},
            },
        ) from exc
    except InvalidMissionScheduleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/{mission_id}/pause",
    summary="Pause mission",
    description=(
        "Pauses a created or waiting Mission and requires an Idempotency-Key "
        "header. A paused Mission cannot be claimed or run."
    ),
)
async def pause_mission_endpoint(
    mission_id: UUID,
    mission_repository: MissionRepositoryDep,
    idempotency_store: MissionCommandIdempotencyStoreDep,
    response: Response,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1)
    ],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> Mission:
    try:
        await _ensure_mission_version(mission_repository, mission_id, if_match)
        mission = await _execute_idempotent_mission_command(
            mission_id=mission_id,
            command=MissionCommandType.PAUSE,
            idempotency_key=idempotency_key,
            idempotency_store=idempotency_store,
            mission_repository=mission_repository,
            execute=lambda: pause_mission(mission_id, mission_repository),
        )
        _set_mission_etag(response, mission)
        return mission
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except MissionPauseNotAllowedError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "mission_pause_not_allowed",
                "message": str(exc),
                "details": {"status": exc.status.value},
            },
        ) from exc
    except MissionCommandIdempotencyConflictError as exc:
        raise HTTPException(status_code=409, detail="Idempotency key conflict") from exc
    except MissionCommandInProgressError as exc:
        raise HTTPException(status_code=409, detail="Command is in progress") from exc


@router.post(
    "/{mission_id}/resume",
    summary="Resume mission",
    description=(
        "Resumes a paused Mission and requires an Idempotency-Key header. "
        "Scheduled Missions return to waiting; unscheduled Missions return "
        "to created."
    ),
)
async def resume_mission_endpoint(
    mission_id: UUID,
    mission_repository: MissionRepositoryDep,
    idempotency_store: MissionCommandIdempotencyStoreDep,
    response: Response,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1)
    ],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> Mission:
    try:
        await _ensure_mission_version(mission_repository, mission_id, if_match)
        mission = await _execute_idempotent_mission_command(
            mission_id=mission_id,
            command=MissionCommandType.RESUME,
            idempotency_key=idempotency_key,
            idempotency_store=idempotency_store,
            mission_repository=mission_repository,
            execute=lambda: resume_mission(mission_id, mission_repository),
        )
        _set_mission_etag(response, mission)
        return mission
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except MissionResumeNotAllowedError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "mission_resume_not_allowed",
                "message": str(exc),
                "details": {"status": exc.status.value},
            },
        ) from exc
    except MissionCommandIdempotencyConflictError as exc:
        raise HTTPException(status_code=409, detail="Idempotency key conflict") from exc
    except MissionCommandInProgressError as exc:
        raise HTTPException(status_code=409, detail="Command is in progress") from exc


@router.post(
    "/{mission_id}/retry",
    summary="Retry failed mission",
    description=(
        "Moves a failed Mission with attempts remaining back to waiting and "
        "requires an Idempotency-Key header. The next processing cycle claims "
        "the Mission at or after retry_at."
    ),
)
async def retry_mission_endpoint(
    mission_id: UUID,
    request: RetryMissionRequest,
    mission_repository: MissionRepositoryDep,
    idempotency_store: MissionCommandIdempotencyStoreDep,
    response: Response,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1)
    ],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> Mission:
    try:
        await _ensure_mission_version(mission_repository, mission_id, if_match)
        mission = await _execute_idempotent_mission_command(
            mission_id=mission_id,
            command=MissionCommandType.RETRY,
            idempotency_key=idempotency_key,
            idempotency_store=idempotency_store,
            mission_repository=mission_repository,
            execute=lambda: retry_mission(
                mission_id,
                mission_repository,
                retry_at=request.retry_at,
            ),
        )
        _set_mission_etag(response, mission)
        return mission
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except MissionRetryNotAllowedError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "mission_retry_not_allowed",
                "message": "Mission cannot be retried in its current state.",
                "details": {"status": exc.status.value},
            },
        ) from exc
    except MissionAttemptsExhaustedError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "mission_attempts_exhausted",
                "message": str(exc),
            },
        ) from exc
    except InvalidMissionRetryTimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MissionCommandIdempotencyConflictError as exc:
        raise HTTPException(status_code=409, detail="Idempotency key conflict") from exc
    except MissionCommandInProgressError as exc:
        raise HTTPException(status_code=409, detail="Command is in progress") from exc


@router.post(
    "/{mission_id}/run",
    summary="Run mission",
    description="Runs a Mission once and requires an Idempotency-Key header.",
)
async def run_mission_endpoint(
    mission_id: UUID,
    mission_repository: MissionRepositoryDep,
    identity_repository: IdentityRepositoryDep,
    provider_resolver: ProviderResolverDep,
    idempotency_store: MissionCommandIdempotencyStoreDep,
    response: Response,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1)
    ],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> Mission:
    try:
        await _ensure_mission_version(mission_repository, mission_id, if_match)
        mission = await _execute_idempotent_mission_command(
            mission_id=mission_id,
            command=MissionCommandType.RUN,
            idempotency_key=idempotency_key,
            idempotency_store=idempotency_store,
            mission_repository=mission_repository,
            execute=lambda: run_mission(
                mission_id,
                mission_repository,
                identity_repository,
                provider_resolver,
            ),
        )
        _set_mission_etag(response, mission)
        return mission
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except InvalidMissionRunError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MissionNotReadyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MissionCommandIdempotencyConflictError as exc:
        raise HTTPException(status_code=409, detail="Idempotency key conflict") from exc
    except MissionCommandInProgressError as exc:
        raise HTTPException(status_code=409, detail="Command is in progress") from exc


@router.post(
    "/{mission_id}/confirm",
    summary="Confirm mission reservation",
    description=(
        "Confirms a Mission reservation and requires an Idempotency-Key header."
    ),
)
async def confirm_mission_endpoint(
    mission_id: UUID,
    mission_repository: MissionRepositoryDep,
    provider_registry: ProviderRegistryDep,
    idempotency_store: MissionCommandIdempotencyStoreDep,
    response: Response,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1)
    ],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> Mission:
    try:
        await _ensure_mission_version(mission_repository, mission_id, if_match)
        mission = await _execute_idempotent_mission_command(
            mission_id=mission_id,
            command=MissionCommandType.CONFIRM,
            idempotency_key=idempotency_key,
            idempotency_store=idempotency_store,
            mission_repository=mission_repository,
            execute=lambda: confirm_mission(
                mission_id,
                mission_repository,
                provider_registry,
            ),
        )
        _set_mission_etag(response, mission)
        return mission
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except InvalidMissionConfirmationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MissionCommandIdempotencyConflictError as exc:
        raise HTTPException(status_code=409, detail="Idempotency key conflict") from exc
    except MissionCommandInProgressError as exc:
        raise HTTPException(status_code=409, detail="Command is in progress") from exc


@router.post(
    "/{mission_id}/cancel",
    summary="Cancel mission",
    description=(
        "Cancels a Mission and requires an Idempotency-Key header. "
        "Reserved Missions are cancelled with their resolved provider first."
    ),
)
async def cancel_mission_endpoint(
    mission_id: UUID,
    mission_repository: MissionRepositoryDep,
    provider_registry: ProviderRegistryDep,
    idempotency_store: MissionCommandIdempotencyStoreDep,
    response: Response,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1)
    ],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> Mission:
    try:
        await _ensure_mission_version(mission_repository, mission_id, if_match)
        mission = await _execute_idempotent_mission_command(
            mission_id=mission_id,
            command=MissionCommandType.CANCEL,
            idempotency_key=idempotency_key,
            idempotency_store=idempotency_store,
            mission_repository=mission_repository,
            execute=lambda: cancel_mission(
                mission_id,
                mission_repository,
                provider_registry,
            ),
        )
        _set_mission_etag(response, mission)
        return mission
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except InvalidMissionCancellationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MissionCommandIdempotencyConflictError as exc:
        raise HTTPException(status_code=409, detail="Idempotency key conflict") from exc
    except MissionCommandInProgressError as exc:
        raise HTTPException(status_code=409, detail="Command is in progress") from exc


async def _execute_idempotent_mission_command(
    *,
    mission_id: UUID,
    command: MissionCommandType,
    idempotency_key: str,
    idempotency_store: MissionCommandIdempotencyStore,
    mission_repository: MissionRepository,
    execute: Callable[[], Awaitable[Mission]],
) -> Mission:
    previous = await idempotency_store.begin(
        key=idempotency_key,
        mission_id=mission_id,
        command=command,
    )
    if previous is not None:
        result = await mission_repository.get(previous)
        assert result is not None
        return result

    try:
        result = await execute()
        await idempotency_store.complete(
            key=idempotency_key,
            mission_id=result.id,
        )
        return result
    except BaseException:
        await idempotency_store.abort(
            key=idempotency_key,
            mission_id=mission_id,
            command=command,
        )
        raise


async def _ensure_mission_version(
    repository: MissionRepository,
    mission_id: UUID,
    if_match: str | None,
) -> None:
    if if_match is None:
        return
    try:
        expected_sequence = int(if_match.strip().strip('"'))
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_mission_version",
                "message": "If-Match must contain a non-negative Mission version.",
            },
        ) from exc
    if expected_sequence < 0:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_mission_version",
                "message": "If-Match must contain a non-negative Mission version.",
            },
        )
    mission = await repository.get(mission_id)
    if mission is None:
        raise MissionNotFoundError
    if mission.last_event_sequence != expected_sequence:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "mission_version_conflict",
                "message": (
                    "Mission changed since the requested version. "
                    "Reload it and try again."
                ),
                "details": {
                    "current_version": mission.last_event_sequence,
                    "expected_version": expected_sequence,
                },
            },
        )


def _set_mission_etag(response: Response, mission: Mission) -> None:
    response.headers["ETag"] = _mission_etag(mission)


def _mission_etag(mission: Mission) -> str:
    return f'"{mission.last_event_sequence}"'

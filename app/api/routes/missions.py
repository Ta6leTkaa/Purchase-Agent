from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Response

from app.dependencies import (
    get_identity_repository,
    get_mission_command_idempotency_store,
    get_mission_provider_resolution_history,
    get_mission_provider_resolution_increment,
    get_mission_provider_resolution_preview,
    get_mission_repository,
    get_provider_resolver,
    get_set_mission_provider,
)
from app.domain.mission import Mission
from app.repositories.identity import IdentityRepository
from app.repositories.mission import MissionRepository
from app.schemas.mission import (
    MissionCreate,
    MissionExecutionAttemptHistoryResponse,
    MissionProviderResolutionHistoryResponse,
    MissionProviderResolutionIncrementResponse,
    MissionProviderResolutionPreviewResponse,
    SetMissionProviderRequest,
)
from app.services.mission_command_idempotency import (
    MissionCommandIdempotencyConflictError,
    MissionCommandIdempotencyStore,
    MissionCommandInProgressError,
    MissionCommandType,
)
from app.services.mission_engine import (
    InvalidMissionConfirmationError,
    InvalidMissionRunError,
    MissionNotReadyError,
    confirm_mission,
    run_mission,
)
from app.services.mission_errors import MissionNotFoundError
from app.services.mission_provider_selection import (
    MissionProviderSelectionNotAllowedError,
    SetMissionProvider,
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

router = APIRouter(prefix="/missions", tags=["missions"])
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
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1)
    ],
) -> Mission:
    try:
        return await _execute_idempotent_mission_command(
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
    idempotency_store: MissionCommandIdempotencyStoreDep,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1)
    ],
) -> Mission:
    try:
        return await _execute_idempotent_mission_command(
            mission_id=mission_id,
            command=MissionCommandType.CONFIRM,
            idempotency_key=idempotency_key,
            idempotency_store=idempotency_store,
            mission_repository=mission_repository,
            execute=lambda: confirm_mission(mission_id, mission_repository),
        )
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Mission not found") from exc
    except InvalidMissionConfirmationError as exc:
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

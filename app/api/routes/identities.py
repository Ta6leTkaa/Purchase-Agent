from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel

from app.api.dependencies.auth import require_api_key
from app.dependencies import get_identity_repository, get_mission_repository
from app.domain.identity import Identity, IdentitySummary
from app.repositories.identity import (
    IdentityRepository,
    IdentityVersionConflictError,
)
from app.repositories.mission import MissionRepository
from app.schemas.identity import (
    IdentityCreate,
    IdentityPreferencesUpdate,
    IdentityUpdate,
)
from app.services.identity_pagination import (
    IdentityCursor,
    IdentityCursorCodec,
    InvalidIdentityCursorError,
)


class IdentitySummaryPage(BaseModel):
    items: list[IdentitySummary]
    has_more: bool
    next_cursor: str | None


router = APIRouter(
    prefix="/identities",
    tags=["identities"],
    dependencies=[Depends(require_api_key)],
)
type IdentityRepositoryDep = Annotated[
    IdentityRepository,
    Depends(get_identity_repository),
]
type MissionRepositoryDep = Annotated[
    MissionRepository,
    Depends(get_mission_repository),
]


@router.post("")
async def create_identity(
    identity: IdentityCreate,
    repository: IdentityRepositoryDep,
    response: Response,
) -> Identity:
    created = await repository.create(identity.to_domain())
    _set_identity_etag(response, created)
    return created


@router.get("")
async def list_identities(
    repository: IdentityRepositoryDep,
    query: str | None = Query(default=None, alias="q", min_length=1, max_length=200),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[Identity]:
    normalized_query = query.strip() if query is not None else None
    if normalized_query == "":
        raise HTTPException(status_code=422, detail="q must not be blank")
    return await repository.list(query=normalized_query, limit=limit)


@router.get("/summaries")
async def list_identity_summaries(
    repository: IdentityRepositoryDep,
    query: str | None = Query(default=None, alias="q", min_length=1, max_length=200),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[IdentitySummary]:
    normalized_query = query.strip() if query is not None else None
    if normalized_query == "":
        raise HTTPException(status_code=422, detail="q must not be blank")
    return await repository.list_summaries(
        query=normalized_query,
        limit=limit,
    )


@router.get("/summaries/page")
async def page_identity_summaries(
    repository: IdentityRepositoryDep,
    query: str | None = Query(default=None, alias="q", min_length=1, max_length=200),
    limit: int = Query(default=100, ge=1, le=500),
    cursor: str | None = None,
) -> IdentitySummaryPage:
    normalized_query = query.strip() if query is not None else None
    if normalized_query == "":
        raise HTTPException(status_code=422, detail="q must not be blank")
    codec = IdentityCursorCodec()
    try:
        decoded_cursor = codec.decode(cursor) if cursor is not None else None
    except InvalidIdentityCursorError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_identity_cursor",
                "message": "Identity cursor is invalid.",
            },
        ) from exc
    candidates = await repository.list_summary_page_candidates(
        query=normalized_query,
        cursor=decoded_cursor,
        limit=limit + 1,
    )
    has_more = len(candidates) > limit
    items = candidates[:limit]
    next_cursor = None
    if has_more:
        next_cursor = codec.encode(IdentityCursor(identity_id=items[-1].id))
    return IdentitySummaryPage(
        items=items,
        has_more=has_more,
        next_cursor=next_cursor,
    )


@router.get("/{identity_id}")
async def get_identity(
    identity_id: UUID,
    repository: IdentityRepositoryDep,
    response: Response,
) -> Identity:
    identity = await repository.get(identity_id)
    if identity is None:
        raise HTTPException(status_code=404, detail="Identity not found")
    _set_identity_etag(response, identity)
    return identity


@router.put("/{identity_id}/preferences")
async def update_identity_preferences(
    identity_id: UUID,
    request: IdentityPreferencesUpdate,
    repository: IdentityRepositoryDep,
    response: Response,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> Identity:
    identity = await repository.get(identity_id)
    if identity is None:
        raise HTTPException(status_code=404, detail="Identity not found")
    expected_version = _require_identity_version(if_match, identity)
    try:
        updated = await repository.update_preferences(
            identity_id,
            request.preferences,
            expected_version,
        )
    except IdentityVersionConflictError as exc:
        raise _identity_version_conflict() from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="Identity not found")
    _set_identity_etag(response, updated)
    return updated


@router.patch("/{identity_id}")
async def update_identity(
    identity_id: UUID,
    request: IdentityUpdate,
    repository: IdentityRepositoryDep,
    response: Response,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> Identity:
    identity = await repository.get(identity_id)
    if identity is None:
        raise HTTPException(status_code=404, detail="Identity not found")
    expected_version = _require_identity_version(if_match, identity)
    try:
        updated = await repository.update(
            request.apply(identity),
            expected_version,
        )
    except IdentityVersionConflictError as exc:
        raise _identity_version_conflict() from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="Identity not found")
    _set_identity_etag(response, updated)
    return updated


@router.delete("/{identity_id}", status_code=204)
async def delete_identity(
    identity_id: UUID,
    identity_repository: IdentityRepositoryDep,
    mission_repository: MissionRepositoryDep,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> Response:
    identity = await identity_repository.get(identity_id)
    if identity is None:
        raise HTTPException(status_code=404, detail="Identity not found")
    expected_version = _require_identity_version(if_match, identity)
    if await mission_repository.references_identity(identity_id):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "identity_in_use",
                "message": "Identity is referenced by one or more missions.",
            },
        )
    try:
        deleted = await identity_repository.delete(identity_id, expected_version)
    except IdentityVersionConflictError as exc:
        raise _identity_version_conflict() from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Identity not found")
    return Response(status_code=204)


def _require_identity_version(if_match: str | None, identity: Identity) -> int:
    if if_match is None:
        raise HTTPException(
            status_code=428,
            detail={
                "code": "identity_version_required",
                "message": "If-Match is required for Identity changes.",
            },
        )
    normalized = if_match.strip()
    if len(normalized) < 2 or not (
        normalized.startswith('"') and normalized.endswith('"')
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_identity_version",
                "message": "If-Match must contain a quoted Identity version.",
            },
        )
    try:
        expected_version = int(normalized[1:-1])
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_identity_version",
                "message": "If-Match must contain a quoted Identity version.",
            },
        ) from exc
    if expected_version != identity.version:
        raise _identity_version_conflict()
    return expected_version


def _identity_version_conflict() -> HTTPException:
    return HTTPException(
        status_code=412,
        detail={
            "code": "identity_version_conflict",
            "message": "Identity changed; reload it and try again.",
        },
    )


def _set_identity_etag(response: Response, identity: Identity) -> None:
    response.headers["ETag"] = f'"{identity.version}"'

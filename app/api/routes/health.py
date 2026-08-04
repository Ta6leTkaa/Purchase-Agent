from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.schema import EXPECTED_SCHEMA_REVISION
from app.dependencies import get_storage_session
from app.services.runtime_state import runtime_state

router = APIRouter()


@router.get("/health")
def health_check() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "purchase-agent-api",
    }


@router.get("/ready")
async def readiness_check(
    session: Annotated[AsyncSession | None, Depends(get_storage_session)],
) -> dict[str, str]:
    """Report whether this API instance can serve its configured storage."""
    runtime_snapshot = runtime_state.snapshot()
    if not runtime_snapshot.accepting_traffic:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "instance_draining",
                "message": "API instance is draining and not accepting traffic.",
                "draining_since": runtime_snapshot.model_dump(mode="json")[
                    "draining_since"
                ],
            },
        )
    if session is None:
        return {"status": "ready", "storage_backend": "memory"}
    try:
        result = await session.execute(
            text("SELECT version_num FROM alembic_version")
        )
        current_revisions = sorted(result.scalars().all())
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Database is not ready",
        ) from exc
    if current_revisions != [EXPECTED_SCHEMA_REVISION]:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "database_schema_not_ready",
                "message": "Database schema revision does not match the API.",
                "expected_revision": EXPECTED_SCHEMA_REVISION,
                "current_revisions": current_revisions,
            },
        )
    return {
        "status": "ready",
        "storage_backend": "database",
        "schema_revision": EXPECTED_SCHEMA_REVISION,
    }

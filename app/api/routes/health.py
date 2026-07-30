from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_storage_session

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
    if session is None:
        return {"status": "ready", "storage_backend": "memory"}
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Database is not ready",
        ) from exc
    return {"status": "ready", "storage_backend": "database"}

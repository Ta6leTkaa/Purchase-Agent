import secrets
from typing import Annotated

from fastapi import Header, HTTPException

from app.core.config import settings


AdminApiKeyHeader = Annotated[
    str | None,
    Header(
        default=None,
        alias="X-Admin-API-Key",
        description="API key for local administrative endpoints.",
    ),
]


async def require_admin_api_key(
    provided_key: AdminApiKeyHeader,
) -> None:
    expected_key = settings.admin_api_key
    if expected_key is None:
        raise HTTPException(
            status_code=503,
            detail="Admin API key is not configured",
        )

    if provided_key is None:
        raise HTTPException(
            status_code=401,
            detail="Admin API key is required",
        )

    if not secrets.compare_digest(
        provided_key,
        expected_key.get_secret_value(),
    ):
        raise HTTPException(
            status_code=403,
            detail="Invalid admin API key",
        )

import secrets
from typing import Annotated

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from app.core.config import settings

client_api_key_scheme = APIKeyHeader(
    name="X-API-Key",
    scheme_name="ClientApiKey",
    description="API key for client-facing Identity, Mission, and Provider APIs.",
    auto_error=False,
)
admin_api_key_scheme = APIKeyHeader(
    name="X-Admin-API-Key",
    scheme_name="AdminApiKey",
    description="Separate API key for operational and administrative APIs.",
    auto_error=False,
)
AdminApiKeyHeader = Annotated[str | None, Security(admin_api_key_scheme)]
ApiKeyHeader = Annotated[str | None, Security(client_api_key_scheme)]


async def require_api_key(provided_key: ApiKeyHeader) -> None:
    """Protect client-facing resources when an API key is configured."""
    expected_key = settings.api_key
    if expected_key is None:
        return
    if provided_key is None:
        raise HTTPException(
            status_code=401,
            detail="API key is required",
            headers={"WWW-Authenticate": 'ApiKey realm="client"'},
        )
    if not secrets.compare_digest(
        provided_key,
        expected_key.get_secret_value(),
    ):
        raise HTTPException(status_code=403, detail="Invalid API key")


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
            headers={"WWW-Authenticate": 'ApiKey realm="admin"'},
        )

    if not secrets.compare_digest(
        provided_key,
        expected_key.get_secret_value(),
    ):
        raise HTTPException(
            status_code=403,
            detail="Invalid admin API key",
        )

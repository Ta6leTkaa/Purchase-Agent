from collections.abc import Awaitable, Callable

import httpx
from pydantic import BaseModel


class DeploymentSmokeCheck(BaseModel):
    name: str
    ok: bool
    status_code: int | None = None
    message: str


class DeploymentSmokeResult(BaseModel):
    ok: bool
    checks: list[DeploymentSmokeCheck]


type SmokeCheckCall = tuple[
    str,
    Callable[[], Awaitable[httpx.Response]],
    Callable[[object], bool],
]


async def run_deployment_smoke(
    *,
    base_url: str,
    api_key: str,
    admin_api_key: str,
    timeout_seconds: float = 10,
    transport: httpx.AsyncBaseTransport | None = None,
) -> DeploymentSmokeResult:
    normalized_base_url = _validate_base_url(base_url)
    async with httpx.AsyncClient(
        base_url=normalized_base_url,
        timeout=timeout_seconds,
        transport=transport,
    ) as client:
        check_calls: list[SmokeCheckCall] = [
            (
                "liveness",
                lambda: client.get("/health"),
                lambda payload: isinstance(payload, dict)
                and payload.get("status") == "ok",
            ),
            (
                "readiness",
                lambda: client.get("/ready"),
                lambda payload: isinstance(payload, dict)
                and payload.get("status") == "ready",
            ),
            (
                "client_auth",
                lambda: client.get(
                    "/missions",
                    params={"limit": 1},
                    headers={"X-API-Key": api_key},
                ),
                lambda payload: isinstance(payload, list),
            ),
            (
                "admin_auth",
                lambda: client.get(
                    "/admin/runtime-status",
                    headers={"X-Admin-API-Key": admin_api_key},
                ),
                lambda payload: isinstance(payload, dict)
                and payload.get("accepting_traffic") is True,
            ),
        ]
        checks = [
            await _run_check(name, request, validate)
            for name, request, validate in check_calls
        ]
    return DeploymentSmokeResult(
        ok=all(check.ok for check in checks),
        checks=checks,
    )


async def _run_check(
    name: str,
    request: Callable[[], Awaitable[httpx.Response]],
    validate: Callable[[object], bool],
) -> DeploymentSmokeCheck:
    try:
        response = await request()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return DeploymentSmokeCheck(
            name=name,
            ok=False,
            message="Request failed or returned invalid JSON.",
        )
    ok = response.status_code == 200 and validate(payload)
    return DeploymentSmokeCheck(
        name=name,
        ok=ok,
        status_code=response.status_code,
        message="Check passed." if ok else "Unexpected status or response shape.",
    )


def _validate_base_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    try:
        url = httpx.URL(normalized)
    except ValueError as exc:
        raise ValueError("base_url must be an absolute HTTP(S) URL") from exc
    if not url.is_absolute_url or url.scheme not in {"http", "https"}:
        raise ValueError("base_url must be an absolute HTTP(S) URL")
    if url.path not in {"", "/"} or url.query or url.fragment or url.userinfo:
        raise ValueError("base_url must not contain a path or query")
    if url.scheme != "https" and url.host not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("base_url must use HTTPS for non-local hosts")
    return normalized

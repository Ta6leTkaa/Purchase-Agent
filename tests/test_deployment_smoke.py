import httpx
import pytest
from pydantic import SecretStr

from app.core.config import settings
from app.main import app
from app.services.deployment_smoke import run_deployment_smoke
from app.services.runtime_state import runtime_state

API_KEY = "smoke-client-secret"
ADMIN_KEY = "smoke-admin-secret"


@pytest.mark.asyncio
async def test_deployment_smoke_runs_against_complete_asgi_application() -> None:
    original_api_key = settings.api_key
    original_admin_api_key = settings.admin_api_key
    settings.api_key = SecretStr(API_KEY)
    settings.admin_api_key = SecretStr(ADMIN_KEY)
    runtime_state.resume()
    try:
        result = await run_deployment_smoke(
            base_url="https://purchase-agent.example.test",
            api_key=API_KEY,
            admin_api_key=ADMIN_KEY,
            transport=httpx.ASGITransport(app=app),
        )
    finally:
        settings.api_key = original_api_key
        settings.admin_api_key = original_admin_api_key
        runtime_state.resume()

    assert result.ok
    assert all(check.status_code == 200 for check in result.checks)


@pytest.mark.asyncio
async def test_deployment_smoke_verifies_public_and_protected_surfaces() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        responses: dict[str, object] = {
            "/health": {"status": "ok"},
            "/ready": {"status": "ready", "storage_backend": "database"},
            "/missions": [],
            "/admin/runtime-status": {"accepting_traffic": True},
        }
        return httpx.Response(200, json=responses[request.url.path])

    result = await run_deployment_smoke(
        base_url="https://purchase-agent.example.test/",
        api_key=API_KEY,
        admin_api_key=ADMIN_KEY,
        transport=httpx.MockTransport(handler),
    )

    assert result.ok
    assert [check.name for check in result.checks] == [
        "liveness",
        "readiness",
        "client_auth",
        "admin_auth",
    ]
    assert all(check.ok for check in result.checks)
    mission_request = next(
        request for request in requests if request.url.path == "/missions"
    )
    admin_request = next(
        request
        for request in requests
        if request.url.path == "/admin/runtime-status"
    )
    assert mission_request.headers["x-api-key"] == API_KEY
    assert admin_request.headers["x-admin-api-key"] == ADMIN_KEY


@pytest.mark.asyncio
async def test_deployment_smoke_reports_all_failures_without_secrets() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            raise httpx.ConnectError("connection failed", request=request)
        if request.url.path == "/ready":
            return httpx.Response(503, json={"detail": "database unavailable"})
        if request.url.path == "/missions":
            return httpx.Response(403, json={"detail": API_KEY})
        return httpx.Response(200, content=b"not-json")

    result = await run_deployment_smoke(
        base_url="http://localhost:8000",
        api_key=API_KEY,
        admin_api_key=ADMIN_KEY,
        transport=httpx.MockTransport(handler),
    )

    assert not result.ok
    assert all(not check.ok for check in result.checks)
    serialized = result.model_dump_json()
    assert API_KEY not in serialized
    assert ADMIN_KEY not in serialized
    assert "database unavailable" not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base_url",
    [
        "purchase-agent.example.test",
        "http://purchase-agent.example.test",
        "https://purchase-agent.example.test/api",
        "https://purchase-agent.example.test?tenant=demo",
    ],
)
async def test_deployment_smoke_rejects_unsafe_base_url(base_url: str) -> None:
    with pytest.raises(ValueError):
        await run_deployment_smoke(
            base_url=base_url,
            api_key=API_KEY,
            admin_api_key=ADMIN_KEY,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={})
            ),
        )

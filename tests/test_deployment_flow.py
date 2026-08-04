import httpx
import pytest
from pydantic import SecretStr

from app.core.config import settings
from app.dependencies import (
    identity_repository,
    mission_command_idempotency_store,
    mission_repository,
    resource_creation_idempotency_store,
)
from app.main import app
from app.services.deployment_flow import run_deployment_flow

API_KEY = "deployment-flow-client-key"


@pytest.mark.asyncio
async def test_deployment_flow_completes_against_full_application() -> None:
    original_api_key = settings.api_key
    settings.api_key = SecretStr(API_KEY)
    await identity_repository.clear()
    await mission_repository.clear()
    await mission_command_idempotency_store.clear()
    await resource_creation_idempotency_store.clear()
    try:
        result = await run_deployment_flow(
            base_url="https://purchase-agent.example.test",
            api_key=API_KEY,
            transport=httpx.ASGITransport(app=app),
        )
    finally:
        settings.api_key = original_api_key
        await identity_repository.clear()
        await mission_repository.clear()
        await mission_command_idempotency_store.clear()
        await resource_creation_idempotency_store.clear()

    assert result.ok
    assert result.stage == "complete"
    assert result.mission_id is not None
    assert result.final_status == "completed"


@pytest.mark.asyncio
async def test_deployment_flow_reports_safe_failing_stage() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/identities":
            return httpx.Response(200, json={"id": "not-a-uuid"})
        return httpx.Response(500, text="database-secret")

    result = await run_deployment_flow(
        base_url="http://localhost:8000",
        api_key=API_KEY,
        transport=httpx.MockTransport(handler),
    )

    assert not result.ok
    assert result.stage == "create_identity"
    assert result.mission_id is None
    assert "database-secret" not in result.model_dump_json()
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_deployment_flow_rejects_empty_provider() -> None:
    with pytest.raises(ValueError, match="provider_id"):
        await run_deployment_flow(
            base_url="http://localhost:8000",
            api_key=API_KEY,
            provider_id="   ",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={})
            ),
        )

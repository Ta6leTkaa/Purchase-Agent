import asyncio
from collections.abc import Iterator
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import settings
from app.dependencies import (
    get_current_time,
    identity_repository,
    mission_repository,
)
from app.main import app

CURRENT_TIME = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
ADMIN_ENDPOINT = "/admin/missions/process-due"
ADMIN_KEY = "test-admin-key"


@pytest.fixture(autouse=True)
def reset_state() -> Iterator[None]:
    original_admin_api_key = settings.admin_api_key
    asyncio.run(identity_repository.clear())
    asyncio.run(mission_repository.clear())
    app.dependency_overrides[get_current_time] = lambda: CURRENT_TIME
    yield
    app.dependency_overrides.clear()
    settings.admin_api_key = original_admin_api_key
    asyncio.run(identity_repository.clear())
    asyncio.run(mission_repository.clear())


def test_admin_endpoint_returns_503_when_key_is_not_configured() -> None:
    settings.admin_api_key = None
    client = TestClient(app)

    response = client.post(ADMIN_ENDPOINT, json={})

    assert response.status_code == 503
    assert response.json()["detail"] == "Admin API key is not configured"


def test_admin_endpoint_returns_401_when_header_is_missing() -> None:
    settings.admin_api_key = SecretStr(ADMIN_KEY)
    client = TestClient(app)

    response = client.post(ADMIN_ENDPOINT, json={})

    assert response.status_code == 401
    assert response.json()["detail"] == "Admin API key is required"


def test_admin_endpoint_returns_403_when_key_is_invalid() -> None:
    settings.admin_api_key = SecretStr(ADMIN_KEY)
    client = TestClient(app)

    response = client.post(
        ADMIN_ENDPOINT,
        json={},
        headers={"X-Admin-API-Key": "wrong-key"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Invalid admin API key"


def test_admin_endpoint_runs_when_key_is_valid(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings.admin_api_key = SecretStr(ADMIN_KEY)
    client = TestClient(app)

    response = client.post(
        ADMIN_ENDPOINT,
        json={},
        headers={"X-Admin-API-Key": ADMIN_KEY},
    )

    assert response.status_code == 200
    assert response.json()["processed_count"] == 0
    assert ADMIN_KEY not in response.text
    assert ADMIN_KEY not in caplog.text


def test_health_does_not_require_admin_key() -> None:
    settings.admin_api_key = SecretStr(ADMIN_KEY)
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200


@pytest.mark.parametrize("path", ["/identities", "/missions"])
def test_public_collection_endpoints_do_not_require_admin_key(path: str) -> None:
    settings.admin_api_key = SecretStr(ADMIN_KEY)
    client = TestClient(app)

    response = client.get(path)

    assert response.status_code == 200

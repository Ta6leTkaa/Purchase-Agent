from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import settings
from app.dependencies import get_current_time
from app.main import app
from app.services.runtime_state import runtime_state

ADMIN_HEADERS = {"X-Admin-API-Key": "runtime-admin-key"}
DRAIN_TIME = datetime(2026, 8, 4, 9, 30, tzinfo=UTC)


@pytest.fixture(autouse=True)
def reset_runtime_state() -> Iterator[None]:
    original_admin_api_key = settings.admin_api_key
    settings.admin_api_key = SecretStr("runtime-admin-key")
    runtime_state.resume()
    app.dependency_overrides[get_current_time] = lambda: DRAIN_TIME
    yield
    app.dependency_overrides.clear()
    runtime_state.resume()
    settings.admin_api_key = original_admin_api_key


def test_drain_removes_instance_from_readiness_until_resumed() -> None:
    client = TestClient(app)

    drained = client.post("/admin/runtime/drain", headers=ADMIN_HEADERS)
    ready_while_draining = client.get("/ready")
    live_while_draining = client.get("/health")
    status = client.get("/admin/runtime-status", headers=ADMIN_HEADERS)
    resumed = client.post("/admin/runtime/resume", headers=ADMIN_HEADERS)
    ready_after_resume = client.get("/ready")

    assert drained.status_code == 200
    assert drained.json() == {
        "traffic_state": "draining",
        "accepting_traffic": False,
        "draining_since": "2026-08-04T09:30:00Z",
    }
    assert ready_while_draining.status_code == 503
    assert ready_while_draining.json()["detail"] == {
        "code": "instance_draining",
        "message": "API instance is draining and not accepting traffic.",
        "draining_since": "2026-08-04T09:30:00Z",
    }
    assert ready_while_draining.headers["cache-control"] == "no-store"
    assert live_while_draining.status_code == 200
    assert status.json() == drained.json()
    assert resumed.json() == {
        "traffic_state": "accepting",
        "accepting_traffic": True,
        "draining_since": None,
    }
    assert ready_after_resume.status_code == 200


def test_repeated_drain_preserves_original_timestamp() -> None:
    client = TestClient(app)
    first = client.post("/admin/runtime/drain", headers=ADMIN_HEADERS)
    app.dependency_overrides[get_current_time] = lambda: datetime(
        2026, 8, 4, 10, 0, tzinfo=UTC
    )

    second = client.post("/admin/runtime/drain", headers=ADMIN_HEADERS)

    assert second.json() == first.json()


def test_unauthorized_drain_does_not_change_readiness() -> None:
    response = TestClient(app).post("/admin/runtime/drain")

    assert response.status_code == 401
    assert runtime_state.snapshot().accepting_traffic

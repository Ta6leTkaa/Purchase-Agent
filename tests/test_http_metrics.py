from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import settings
from app.main import app
from app.services.http_metrics import HttpMetrics, http_metrics

ADMIN_KEY = "metrics-admin-key"


@pytest.fixture(autouse=True)
def reset_metrics_and_key() -> Iterator[None]:
    original_admin_api_key = settings.admin_api_key
    http_metrics.reset()
    settings.admin_api_key = SecretStr(ADMIN_KEY)
    yield
    settings.admin_api_key = original_admin_api_key
    http_metrics.reset()


def test_admin_http_statistics_aggregates_safe_dimensions() -> None:
    client = TestClient(app)
    client.get("/health?secret=query-value")
    client.get("/missing-resource")

    response = client.get(
        "/admin/http-statistics",
        headers={"X-Admin-API-Key": ADMIN_KEY},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_requests"] == 2
    assert payload["in_flight_requests"] == 1
    assert payload["requests_by_method"] == {"GET": 2}
    assert payload["responses_by_status_class"] == {"2xx": 1, "4xx": 1}
    assert payload["average_duration_ms"] >= 0
    assert payload["max_duration_ms"] >= payload["average_duration_ms"]
    serialized = response.text
    assert "query-value" not in serialized
    assert ADMIN_KEY not in serialized
    assert "/health" not in serialized


def test_admin_http_statistics_requires_admin_key() -> None:
    response = TestClient(app).get("/admin/http-statistics")

    assert response.status_code == 401


def test_http_metrics_tracks_in_flight_and_server_errors() -> None:
    metrics = HttpMetrics()
    metrics.start_request()
    metrics.start_request()
    metrics.finish_request(method="POST", status_code=500, duration_ms=12.5)

    snapshot = metrics.snapshot()

    assert snapshot.total_requests == 1
    assert snapshot.in_flight_requests == 1
    assert snapshot.requests_by_method == {"POST": 1}
    assert snapshot.responses_by_status_class == {"5xx": 1}
    assert snapshot.average_duration_ms == 12.5
    assert snapshot.max_duration_ms == 12.5

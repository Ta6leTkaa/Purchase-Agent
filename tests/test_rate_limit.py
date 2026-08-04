from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(
        create_app(
            Settings(
                api_rate_limit_enabled=True,
                api_rate_limit_requests=2,
                api_rate_limit_window_seconds=60,
            )
        )
    ) as test_client:
        yield test_client


def test_rejects_requests_after_client_limit(client: TestClient) -> None:
    headers = {"X-API-Key": "client-secret"}

    first = client.get("/missions", headers=headers)
    second = client.get("/missions", headers=headers)
    rejected = client.get("/missions", headers=headers)

    assert first.status_code == 200
    assert first.headers["ratelimit-limit"] == "2"
    assert first.headers["ratelimit-remaining"] == "1"
    assert second.headers["ratelimit-remaining"] == "0"
    assert rejected.status_code == 429
    assert rejected.json() == {
        "detail": {
            "code": "rate_limit_exceeded",
            "message": "Too many requests. Retry after the indicated delay.",
            "retry_after_seconds": 60,
        }
    }
    assert rejected.headers["retry-after"] == "60"
    assert rejected.headers["ratelimit-remaining"] == "0"


def test_client_and_admin_keys_have_independent_limits(client: TestClient) -> None:
    for _ in range(2):
        response = client.get(
            "/missing-resource",
            headers={"X-API-Key": "client-secret"},
        )
        assert response.status_code == 404

    admin_response = client.get(
        "/missing-resource",
        headers={"X-Admin-API-Key": "admin-secret"},
    )

    assert admin_response.status_code == 404
    assert admin_response.headers["ratelimit-remaining"] == "1"


def test_health_probes_are_not_rate_limited(client: TestClient) -> None:
    for _ in range(5):
        assert client.get("/health").status_code == 200

    response = client.get("/ready")

    assert response.status_code == 200
    assert "ratelimit-limit" not in response.headers


def test_unauthenticated_requests_are_limited_by_client_address(
    client: TestClient,
) -> None:
    first = client.get("/missing-resource")
    second = client.get("/missing-resource")
    rejected = client.get("/missing-resource")

    assert first.status_code == 404
    assert second.status_code == 404
    assert rejected.status_code == 429


def test_options_preflight_is_not_rate_limited(client: TestClient) -> None:
    for _ in range(3):
        response = client.options(
            "/missions",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code != 429

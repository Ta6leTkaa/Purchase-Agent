import json
import logging
from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import settings
from app.main import app


@pytest.fixture(autouse=True)
def restore_api_key() -> Iterator[None]:
    original_api_key = settings.api_key
    yield
    settings.api_key = original_api_key


def test_response_includes_generated_request_id() -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert UUID(response.headers["X-Request-ID"])


def test_valid_request_id_is_preserved() -> None:
    response = TestClient(app).get(
        "/health",
        headers={"X-Request-ID": "gateway-request_123"},
    )

    assert response.headers["X-Request-ID"] == "gateway-request_123"


@pytest.mark.parametrize(
    "request_id",
    ["contains spaces", "line\nbreak", "a" * 129, "../invalid"],
)
def test_unsafe_request_id_is_replaced(request_id: str) -> None:
    response = TestClient(app).get(
        "/health",
        headers={"X-Request-ID": request_id},
    )

    assert response.headers["X-Request-ID"] != request_id
    assert UUID(response.headers["X-Request-ID"])


def test_request_log_is_structured_and_omits_secrets_and_query(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings.api_key = SecretStr("sensitive-client-key")
    caplog.set_level(logging.INFO, logger="purchase_agent.http")

    response = TestClient(app).get(
        "/identities?limit=1&token=query-secret",
        headers={
            "X-API-Key": "sensitive-client-key",
            "X-Request-ID": "log-correlation-id",
        },
    )

    assert response.status_code == 200
    record = next(
        record
        for record in caplog.records
        if record.name == "purchase_agent.http"
        and "log-correlation-id" in record.getMessage()
    )
    payload = json.loads(record.getMessage())
    assert payload["request_id"] == "log-correlation-id"
    assert payload["method"] == "GET"
    assert payload["path"] == "/identities"
    assert payload["status_code"] == 200
    assert payload["duration_ms"] >= 0
    assert "sensitive-client-key" not in record.getMessage()
    assert "query-secret" not in record.getMessage()

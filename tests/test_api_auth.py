from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import settings
from app.main import app

API_KEY = "test-client-api-key"
PROTECTED_ENDPOINTS = [
    "/identities",
    "/missions",
    "/providers",
]


@pytest.fixture(autouse=True)
def restore_api_key() -> Iterator[None]:
    original_api_key = settings.api_key
    yield
    settings.api_key = original_api_key


@pytest.mark.parametrize("endpoint", PROTECTED_ENDPOINTS)
def test_client_endpoint_allows_local_access_when_key_is_not_configured(
    endpoint: str,
) -> None:
    settings.api_key = None

    response = TestClient(app).get(endpoint)

    assert response.status_code == 200


@pytest.mark.parametrize("endpoint", PROTECTED_ENDPOINTS)
def test_client_endpoint_requires_configured_api_key(endpoint: str) -> None:
    settings.api_key = SecretStr(API_KEY)

    response = TestClient(app).get(endpoint)

    assert response.status_code == 401
    assert response.json()["detail"] == "API key is required"
    assert response.headers["www-authenticate"] == 'ApiKey realm="client"'


@pytest.mark.parametrize("endpoint", PROTECTED_ENDPOINTS)
def test_client_endpoint_rejects_invalid_api_key(endpoint: str) -> None:
    settings.api_key = SecretStr(API_KEY)

    response = TestClient(app).get(
        endpoint,
        headers={"X-API-Key": "wrong-key"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Invalid API key"


@pytest.mark.parametrize("endpoint", PROTECTED_ENDPOINTS)
def test_client_endpoint_accepts_valid_api_key(endpoint: str) -> None:
    settings.api_key = SecretStr(API_KEY)

    response = TestClient(app).get(
        endpoint,
        headers={"X-API-Key": API_KEY},
    )

    assert response.status_code == 200


def test_web_session_authenticates_browser_without_custom_header() -> None:
    settings.api_key = SecretStr(API_KEY)
    client = TestClient(app)

    login = client.post("/app/session", json={"api_key": API_KEY})

    assert login.status_code == 200
    assert login.json() == {"authenticated": True}
    assert "HttpOnly" in login.headers["set-cookie"]
    assert "SameSite=strict" in login.headers["set-cookie"]
    assert client.get("/providers").status_code == 200


def test_web_session_rejects_invalid_key() -> None:
    settings.api_key = SecretStr(API_KEY)

    response = TestClient(app).post(
        "/app/session",
        json={"api_key": "wrong-key"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == (
        "Неверный API-ключ. Скопируйте актуальное значение API_KEY "
        "из .env: получено 9 из 19 символов."
    )


def test_web_session_rejects_non_ascii_key_without_server_error() -> None:
    settings.api_key = SecretStr(API_KEY)

    response = TestClient(app).post(
        "/app/session",
        json={"api_key": "неверный-ключ-пользователя"},
    )

    assert response.status_code == 403
    assert "Неверный API-ключ" in response.json()["detail"]


@pytest.mark.parametrize("endpoint", ["/health", "/ready"])
def test_service_probes_remain_public(endpoint: str) -> None:
    settings.api_key = SecretStr(API_KEY)

    response = TestClient(app).get(endpoint)

    assert response.status_code == 200

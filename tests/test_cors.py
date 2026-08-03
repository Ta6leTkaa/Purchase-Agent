import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.main import create_app


def test_configured_browser_origin_can_preflight_protected_request() -> None:
    application = create_app(
        Settings(cors_allowed_origins=["http://localhost:3000"])
    )

    response = TestClient(application).options(
        "/missions",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": (
                "content-type,idempotency-key,if-match,if-none-match,"
                "x-api-key,x-request-id"
            ),
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == (
        "http://localhost:3000"
    )
    assert "POST" in response.headers["access-control-allow-methods"]
    assert "x-api-key" in response.headers["access-control-allow-headers"].lower()
    assert "if-none-match" in (
        response.headers["access-control-allow-headers"].lower()
    )
    assert "access-control-allow-credentials" not in response.headers


def test_cors_exposes_concurrency_and_correlation_headers() -> None:
    application = create_app(
        Settings(cors_allowed_origins=["https://app.example.test"])
    )

    response = TestClient(application).get(
        "/health",
        headers={"Origin": "https://app.example.test"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == (
        "https://app.example.test"
    )
    exposed = response.headers["access-control-expose-headers"].lower()
    assert "etag" in exposed
    assert "x-request-id" in exposed


def test_unconfigured_origin_receives_no_cors_permission() -> None:
    response = TestClient(create_app(Settings(cors_allowed_origins=[]))).get(
        "/health",
        headers={"Origin": "https://untrusted.example.test"},
    )

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize(
    "origin",
    [
        "*",
        "http://app.example.test",
        "https://user:secret@app.example.test",
        "https://app.example.test/path",
        "https://app.example.test?tenant=demo",
    ],
)
def test_cors_rejects_unsafe_origins(origin: str) -> None:
    with pytest.raises(ValidationError):
        Settings(cors_allowed_origins=[origin])


def test_cors_origins_load_from_json_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "CORS_ALLOWED_ORIGINS",
        '["http://localhost:3000", "https://app.example.test/"]',
    )

    configured = Settings()

    assert configured.cors_allowed_origins == [
        "http://localhost:3000",
        "https://app.example.test",
    ]

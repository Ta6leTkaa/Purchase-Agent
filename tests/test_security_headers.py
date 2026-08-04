from fastapi import Response
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app, create_app

EXPECTED_SECURITY_HEADERS = {
    "permissions-policy": "camera=(), geolocation=(), microphone=()",
    "referrer-policy": "no-referrer",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
}


def test_success_response_contains_security_headers() -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    for name, value in EXPECTED_SECURITY_HEADERS.items():
        assert response.headers[name] == value
    assert "cache-control" not in response.headers


def test_error_response_is_not_cacheable() -> None:
    response = TestClient(app).get("/missing-resource")

    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"
    for name, value in EXPECTED_SECURITY_HEADERS.items():
        assert response.headers[name] == value


def test_security_policy_overrides_weaker_route_headers() -> None:
    application = create_app(Settings())

    @application.get("/weak-headers")
    async def weak_headers(response: Response) -> dict[str, str]:
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        return {"status": "ok"}

    response = TestClient(application).get("/weak-headers")

    assert response.headers["x-frame-options"] == "DENY"


def test_api_documentation_can_be_disabled() -> None:
    client = TestClient(create_app(Settings(api_docs_enabled=False)))

    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/health").status_code == 200

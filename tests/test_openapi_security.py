from fastapi.testclient import TestClient

from app.main import app


def test_openapi_publishes_distinct_api_key_schemes() -> None:
    schema = TestClient(app).get("/openapi.json").json()

    assert schema["components"]["securitySchemes"] == {
        "ClientApiKey": {
            "type": "apiKey",
            "description": (
                "API key for client-facing Identity, Mission, and Provider APIs."
            ),
            "in": "header",
            "name": "X-API-Key",
        },
        "AdminApiKey": {
            "type": "apiKey",
            "description": (
                "Separate API key for operational and administrative APIs."
            ),
            "in": "header",
            "name": "X-Admin-API-Key",
        },
    }


def test_openapi_assigns_client_security_only_to_client_resources() -> None:
    schema = TestClient(app).get("/openapi.json").json()

    assert schema["paths"]["/identities"]["get"]["security"] == [
        {"ClientApiKey": []}
    ]
    assert schema["paths"]["/missions"]["get"]["security"] == [
        {"ClientApiKey": []}
    ]
    assert schema["paths"]["/providers"]["get"]["security"] == [
        {"ClientApiKey": []}
    ]
    assert "security" not in schema["paths"]["/health"]["get"]
    assert "security" not in schema["paths"]["/ready"]["get"]


def test_openapi_assigns_admin_security_to_operational_resources() -> None:
    schema = TestClient(app).get("/openapi.json").json()

    assert schema["paths"]["/admin/http-statistics"]["get"]["security"] == [
        {"AdminApiKey": []}
    ]
    assert schema["paths"]["/admin/mission-statistics"]["get"]["security"] == [
        {"AdminApiKey": []}
    ]

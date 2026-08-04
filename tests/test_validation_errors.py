from fastapi.testclient import TestClient

from app.main import app


def test_body_validation_error_has_stable_safe_envelope() -> None:
    secret_input = "passport-secret-that-must-not-be-reflected"

    response = TestClient(app).post(
        "/identities",
        json={
            "display_name": "Ivan Petrov",
            "first_name": "Ivan",
            "last_name": "Petrov",
            "birth_date": "not-a-date",
            "documents": [
                {"type": "internal_passport", "number": secret_input}
            ],
            "unexpected_secret": secret_input,
        },
        headers={"X-Request-ID": "validation-body-request"},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "request_validation_error"
    assert detail["message"] == "Request validation failed."
    assert detail["request_id"] == "validation-body-request"
    assert detail["errors"] == [
        {
            "location": ["body", "birth_date"],
            "message": (
                "Input should be a valid date or datetime, "
                "invalid character in year"
            ),
            "type": "date_from_datetime_parsing",
        },
        {
            "location": ["body", "unexpected_secret"],
            "message": "Extra inputs are not permitted",
            "type": "extra_forbidden",
        },
    ]
    assert secret_input not in response.text
    assert response.headers["x-request-id"] == "validation-body-request"


def test_query_validation_error_identifies_parameter_without_input_value() -> None:
    response = TestClient(app).get(
        "/missions",
        params={"limit": "sensitive-invalid-limit"},
        headers={"X-Request-ID": "validation-query-request"},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail == {
        "code": "request_validation_error",
        "message": "Request validation failed.",
        "request_id": "validation-query-request",
        "errors": [
            {
                "location": ["query", "limit"],
                "message": (
                    "Input should be a valid integer, unable to parse string "
                    "as an integer"
                ),
                "type": "int_parsing",
            }
        ],
    }
    assert "sensitive-invalid-limit" not in response.text


def test_malformed_json_does_not_echo_request_fragment() -> None:
    malformed = '{"display_name":"sensitive-value"'

    response = TestClient(app).post(
        "/identities",
        content=malformed,
        headers={
            "Content-Type": "application/json",
            "X-Request-ID": "validation-json-request",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "request_validation_error"
    assert response.json()["detail"]["errors"][0]["type"] == "json_invalid"
    assert "sensitive-value" not in response.text


def test_openapi_documents_validation_error_envelope() -> None:
    schema = TestClient(app).get("/openapi.json").json()
    schemas = schema["components"]["schemas"]

    assert schemas["HTTPValidationError"] == {
        "type": "object",
        "required": ["detail"],
        "properties": {
            "detail": {
                "$ref": "#/components/schemas/RequestValidationErrorDetail"
            }
        },
    }
    assert schemas["RequestValidationErrorDetail"]["properties"]["code"] == {
        "type": "string",
        "const": "request_validation_error",
    }
    assert schema["paths"]["/identities"]["post"]["responses"]["422"][
        "content"
    ]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/HTTPValidationError"
    }

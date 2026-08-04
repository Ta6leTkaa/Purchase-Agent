from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi


def configure_openapi(application: FastAPI) -> None:
    def custom_openapi() -> dict[str, Any]:
        if application.openapi_schema is not None:
            return application.openapi_schema
        schema = get_openapi(
            title=application.title,
            version=application.version,
            routes=application.routes,
        )
        schemas = schema.setdefault("components", {}).setdefault("schemas", {})
        schemas["ValidationIssue"] = {
            "type": "object",
            "required": ["location", "message", "type"],
            "properties": {
                "location": {
                    "type": "array",
                    "items": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
                },
                "message": {"type": "string"},
                "type": {"type": "string"},
            },
        }
        schemas["RequestValidationErrorDetail"] = {
            "type": "object",
            "required": ["code", "message", "request_id", "errors"],
            "properties": {
                "code": {
                    "type": "string",
                    "const": "request_validation_error",
                },
                "message": {"type": "string"},
                "request_id": {"type": "string"},
                "errors": {
                    "type": "array",
                    "items": {"$ref": "#/components/schemas/ValidationIssue"},
                },
            },
        }
        schemas["HTTPValidationError"] = {
            "type": "object",
            "required": ["detail"],
            "properties": {
                "detail": {
                    "$ref": "#/components/schemas/RequestValidationErrorDetail"
                }
            },
        }
        application.openapi_schema = schema
        return schema

    application.openapi = custom_openapi  # type: ignore[method-assign]

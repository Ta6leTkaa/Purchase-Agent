from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.adapters.registry import UnknownProviderError
from app.api.middleware.request_context import get_request_id
from app.repositories.sqlalchemy.mission import MissionEventSequenceConflictError
from app.services.provider_errors import (
    UnsupportedExecutionModeError,
    UnsupportedMissionTypeError,
)
from app.services.provider_resolver import (
    AmbiguousProviderError,
    NoSupportingProviderError,
)


@dataclass(frozen=True)
class ProviderResolutionHttpError:
    status_code: int
    code: str
    message: str
    details: dict[str, Any]


def map_provider_resolution_error(
    error: (
        UnknownProviderError
        | UnsupportedMissionTypeError
        | UnsupportedExecutionModeError
        | NoSupportingProviderError
        | AmbiguousProviderError
    ),
) -> ProviderResolutionHttpError:
    if isinstance(error, UnknownProviderError):
        return ProviderResolutionHttpError(
            status_code=422,
            code="unknown_provider",
            message="The requested provider is not registered.",
            details={"provider_id": error.provider_id},
        )
    if isinstance(error, UnsupportedMissionTypeError):
        return ProviderResolutionHttpError(
            status_code=422,
            code="unsupported_mission_type",
            message="The selected provider does not support this mission type.",
            details={
                "provider_id": error.provider_id,
                "mission_type": error.mission_type.value,
            },
        )
    if isinstance(error, UnsupportedExecutionModeError):
        return ProviderResolutionHttpError(
            status_code=422 if error.provider_id is not None else 409,
            code="unsupported_execution_mode",
            message=(
                "The selected provider does not support this execution mode."
                if error.provider_id is not None
                else "No configured provider supports this execution mode."
            ),
            details={
                "provider_id": error.provider_id,
                "execution_mode": error.execution_mode.value,
            },
        )
    if isinstance(error, NoSupportingProviderError):
        return ProviderResolutionHttpError(
            status_code=409,
            code="no_supporting_provider",
            message="No configured provider supports this mission type.",
            details={"mission_type": error.mission_type.value},
        )
    return ProviderResolutionHttpError(
        status_code=409,
        code="ambiguous_provider",
        message=(
            "Multiple providers support this mission type. "
            "Select a provider explicitly."
        ),
        details={
            "mission_type": error.mission_type.value,
            "candidate_provider_ids": list(error.provider_ids),
        },
    )


def register_api_exception_handlers(app: FastAPI) -> None:
    async def handle_request_validation_error(
        request: Request,
        error: Exception,
    ) -> JSONResponse:
        if not isinstance(error, RequestValidationError):
            raise error
        request_id = (
            getattr(request.state, "request_id", None)
            or get_request_id()
            or "unavailable"
        )
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "code": "request_validation_error",
                    "message": "Request validation failed.",
                    "request_id": request_id,
                    "errors": [
                        {
                            "location": list(item["loc"]),
                            "message": item["msg"],
                            "type": item["type"],
                        }
                        for item in error.errors()
                    ],
                }
            },
        )

    app.add_exception_handler(
        RequestValidationError,
        handle_request_validation_error,
    )

    async def handle_provider_resolution_error(
        request: Request,
        error: Exception,
    ) -> JSONResponse:
        del request
        if not isinstance(
            error,
            (
                UnknownProviderError,
                UnsupportedMissionTypeError,
                UnsupportedExecutionModeError,
                NoSupportingProviderError,
                AmbiguousProviderError,
            ),
        ):
            raise error
        mapped_error = map_provider_resolution_error(error)
        return JSONResponse(
            status_code=mapped_error.status_code,
            content={
                "detail": {
                    "code": mapped_error.code,
                    "message": mapped_error.message,
                    "details": mapped_error.details,
                }
            },
        )

    app.add_exception_handler(
        UnknownProviderError,
        handle_provider_resolution_error,
    )
    app.add_exception_handler(
        UnsupportedMissionTypeError,
        handle_provider_resolution_error,
    )
    app.add_exception_handler(
        UnsupportedExecutionModeError,
        handle_provider_resolution_error,
    )
    app.add_exception_handler(
        NoSupportingProviderError,
        handle_provider_resolution_error,
    )
    app.add_exception_handler(
        AmbiguousProviderError,
        handle_provider_resolution_error,
    )

    async def handle_mission_sequence_conflict(
        request: Request,
        error: Exception,
    ) -> JSONResponse:
        del request, error
        return JSONResponse(
            status_code=409,
            content={
                "detail": {
                    "code": "mission_version_conflict",
                    "message": (
                        "Mission changed while this command was being processed. "
                        "Reload it and try again."
                    ),
                }
            },
        )

    app.add_exception_handler(
        MissionEventSequenceConflictError,
        handle_mission_sequence_conflict,
    )

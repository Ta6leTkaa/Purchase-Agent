from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.adapters.registry import UnknownProviderError
from app.services.provider_errors import UnsupportedMissionTypeError
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


def register_provider_resolution_exception_handlers(app: FastAPI) -> None:
    async def handle_provider_resolution_error(
        request: Request,
        error: (
            UnknownProviderError
            | UnsupportedMissionTypeError
            | NoSupportingProviderError
            | AmbiguousProviderError
        ),
    ) -> JSONResponse:
        del request
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
        NoSupportingProviderError,
        handle_provider_resolution_error,
    )
    app.add_exception_handler(
        AmbiguousProviderError,
        handle_provider_resolution_error,
    )

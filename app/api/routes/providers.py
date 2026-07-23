from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path

from app.adapters.base import ProviderAdapter
from app.adapters.registry import ProviderRegistry, UnknownProviderError
from app.dependencies import get_provider_registry
from app.domain.mission import MissionType
from app.domain.provider_id import normalize_provider_id
from app.schemas.provider import (
    ProviderListResponse,
    ProviderResponse,
    SupportingProviderListResponse,
)

router = APIRouter(prefix="/providers", tags=["providers"])
ProviderRegistryDep = Annotated[
    ProviderRegistry,
    Depends(get_provider_registry),
]


def provider_to_response(adapter: ProviderAdapter) -> ProviderResponse:
    """Expose only stable provider identity and declared capabilities."""
    return ProviderResponse(
        provider_id=adapter.provider_id,
        mission_types=tuple(
            sorted(
                (
                    capability.mission_type
                    for capability in adapter.capabilities
                ),
                key=lambda mission_type: mission_type.value,
            )
        ),
    )


@router.get(
    "",
    response_model=ProviderListResponse,
    summary="List registered providers",
    description=(
        "Returns providers in the current runtime registry and their declared "
        "mission types. This endpoint does not perform live availability checks."
    ),
)
def list_providers(
    registry: ProviderRegistryDep,
) -> ProviderListResponse:
    return ProviderListResponse(
        providers=tuple(
            provider_to_response(adapter)
            for adapter in registry.list_all()
        )
    )


@router.get(
    "/supporting/{mission_type}",
    response_model=SupportingProviderListResponse,
    summary="List providers supporting a mission type",
    description=(
        "Returns registered providers that declare support for the requested "
        "mission type. Returned provider IDs may be used as Mission.provider_id."
    ),
)
def list_supporting_providers(
    mission_type: MissionType,
    registry: ProviderRegistryDep,
) -> SupportingProviderListResponse:
    return SupportingProviderListResponse(
        mission_type=mission_type,
        providers=tuple(
            provider_to_response(adapter)
            for adapter in registry.list_supporting(mission_type)
        ),
    )


@router.get(
    "/{provider_id}",
    response_model=ProviderResponse,
    summary="Get a registered provider",
    description=(
        "Returns the public identifier and supported mission types of a "
        "provider registered in the current runtime configuration. This "
        "endpoint does not check external provider availability or credentials."
    ),
    responses={
        404: {"description": "Provider not registered"},
    },
)
def get_provider(
    provider_id: Annotated[
        str,
        Path(
            description=(
                "Stable machine-readable provider identifier returned by "
                "GET /providers."
            )
        ),
    ],
    registry: ProviderRegistryDep,
) -> ProviderResponse:
    try:
        normalized_provider_id = normalize_provider_id(provider_id)
        assert normalized_provider_id is not None
        adapter = registry.get(normalized_provider_id)
    except (UnknownProviderError, ValueError):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "provider_not_found",
                "message": "The requested provider was not found.",
                "details": {"provider_id": provider_id},
            },
        ) from None

    return provider_to_response(adapter)

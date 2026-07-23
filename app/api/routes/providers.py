from typing import Annotated

from fastapi import APIRouter, Depends

from app.adapters.base import ProviderAdapter
from app.adapters.registry import ProviderRegistry
from app.dependencies import get_provider_registry
from app.domain.mission import MissionType
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

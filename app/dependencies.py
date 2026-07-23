from datetime import datetime
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import ProviderRegistry, provider_registry
from app.core.config import settings
from app.db.session import get_db_session
from app.repositories.identity import IdentityRepository
from app.repositories.mission import MissionRepository
from app.repositories.sqlalchemy.identity import SqlAlchemyIdentityRepository
from app.repositories.sqlalchemy.mission import SqlAlchemyMissionRepository
from app.services.clock import utc_now
from app.services.mission_provider_selection import SetMissionProvider
from app.services.provider_resolution_history import (
    GetMissionProviderResolutionHistory,
    GetMissionProviderResolutionIncrement,
)
from app.services.provider_resolution_preview import (
    PreviewMissionProviderResolution,
)
from app.services.provider_resolver import ProviderResolver
from app.storage.memory import InMemoryIdentityRepository, InMemoryMissionRepository

identity_repository = InMemoryIdentityRepository()
mission_repository = InMemoryMissionRepository()
provider_resolver = ProviderResolver(provider_registry)
DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]


def get_identity_repository(session: DbSessionDep) -> IdentityRepository:
    if settings.storage_backend == "database":
        return SqlAlchemyIdentityRepository(session)
    return identity_repository


def get_mission_repository(session: DbSessionDep) -> MissionRepository:
    if settings.storage_backend == "database":
        return SqlAlchemyMissionRepository(session)
    return mission_repository


def get_current_time() -> datetime:
    return utc_now()


def get_provider_registry() -> ProviderRegistry:
    return provider_registry


def get_provider_resolver() -> ProviderResolver:
    return provider_resolver


def get_set_mission_provider(
    mission_repository: Annotated[
        MissionRepository,
        Depends(get_mission_repository),
    ],
    registry: Annotated[
        ProviderRegistry,
        Depends(get_provider_registry),
    ],
) -> SetMissionProvider:
    return SetMissionProvider(mission_repository, registry, clock=utc_now)


def get_mission_provider_resolution_preview(
    mission_repository: Annotated[
        MissionRepository,
        Depends(get_mission_repository),
    ],
    resolver: Annotated[
        ProviderResolver,
        Depends(get_provider_resolver),
    ],
) -> PreviewMissionProviderResolution:
    return PreviewMissionProviderResolution(mission_repository, resolver)


def get_mission_provider_resolution_history(
    mission_repository: Annotated[
        MissionRepository,
        Depends(get_mission_repository),
    ],
) -> GetMissionProviderResolutionHistory:
    return GetMissionProviderResolutionHistory(mission_repository)


def get_mission_provider_resolution_increment(
    mission_repository: Annotated[
        MissionRepository,
        Depends(get_mission_repository),
    ],
) -> GetMissionProviderResolutionIncrement:
    return GetMissionProviderResolutionIncrement(mission_repository)

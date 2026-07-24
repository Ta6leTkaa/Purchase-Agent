from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters import ProviderRegistry, provider_registry
from app.core.config import settings
from app.db.session import async_session_maker, get_db_session
from app.repositories.identity import IdentityRepository
from app.repositories.mission import MissionRepository
from app.repositories.sqlalchemy.identity import SqlAlchemyIdentityRepository
from app.repositories.sqlalchemy.mission import SqlAlchemyMissionRepository
from app.repositories.sqlalchemy.provider_history import (
    SqlAlchemyProviderHistoryProjectionRepository,
)
from app.services.clock import utc_now
from app.services.mission_provider_selection import SetMissionProvider
from app.services.provider_resolution_history import (
    AsyncioWaiter,
    AsyncWaiter,
    GetMissionProviderResolutionHistory,
    GetMissionProviderResolutionIncrement,
    MissionReadRepositoryFactory,
    StaticMissionReadRepositoryFactory,
)
from app.services.provider_resolution_preview import (
    PreviewMissionProviderResolution,
)
from app.services.provider_history_verification import (
    VerifyMissionProviderHistoryProjection,
)
from app.services.provider_resolver import ProviderResolver
from app.storage.memory import InMemoryIdentityRepository, InMemoryMissionRepository

identity_repository = InMemoryIdentityRepository()
mission_repository = InMemoryMissionRepository()
provider_resolver = ProviderResolver(provider_registry)
provider_history_waiter = AsyncioWaiter()
DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]


class SqlAlchemyMissionReadRepositoryFactory:
    def __init__(
        self,
        session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_maker = session_maker

    @asynccontextmanager
    async def open(self) -> AsyncIterator[MissionRepository]:
        async with self._session_maker() as session:
            try:
                yield SqlAlchemyMissionRepository(session)
            finally:
                await session.rollback()


class SqlAlchemyProviderHistoryProjectionReaderFactory:
    def __init__(
        self,
        session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_maker = session_maker

    @asynccontextmanager
    async def open(
        self,
    ) -> AsyncIterator[SqlAlchemyProviderHistoryProjectionRepository]:
        async with self._session_maker() as session:
            try:
                yield SqlAlchemyProviderHistoryProjectionRepository(session)
            finally:
                await session.rollback()


def get_identity_repository(session: DbSessionDep) -> IdentityRepository:
    if settings.storage_backend == "database":
        return SqlAlchemyIdentityRepository(session)
    return identity_repository


def get_mission_repository(session: DbSessionDep) -> MissionRepository:
    if settings.storage_backend == "database":
        return SqlAlchemyMissionRepository(session)
    return mission_repository


def get_mission_read_repository_factory() -> MissionReadRepositoryFactory:
    if settings.storage_backend == "database":
        return SqlAlchemyMissionReadRepositoryFactory(async_session_maker)
    return StaticMissionReadRepositoryFactory(mission_repository)


def get_provider_history_waiter() -> AsyncWaiter:
    return provider_history_waiter


def get_provider_history_projection_reader(
    session: DbSessionDep,
) -> SqlAlchemyProviderHistoryProjectionRepository | None:
    if settings.storage_backend == "database":
        return SqlAlchemyProviderHistoryProjectionRepository(session)
    return None


def get_provider_history_projection_reader_factory(
) -> SqlAlchemyProviderHistoryProjectionReaderFactory | None:
    if settings.storage_backend == "database":
        return SqlAlchemyProviderHistoryProjectionReaderFactory(
            async_session_maker
        )
    return None


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
    projection_reader: Annotated[
        SqlAlchemyProviderHistoryProjectionRepository | None,
        Depends(get_provider_history_projection_reader),
    ],
) -> GetMissionProviderResolutionHistory:
    return GetMissionProviderResolutionHistory(
        mission_repository,
        projection_reader,
    )


def get_mission_provider_resolution_increment(
    mission_read_repository_factory: Annotated[
        MissionReadRepositoryFactory,
        Depends(get_mission_read_repository_factory),
    ],
    waiter: Annotated[
        AsyncWaiter,
        Depends(get_provider_history_waiter),
    ],
    projection_reader_factory: Annotated[
        SqlAlchemyProviderHistoryProjectionReaderFactory | None,
        Depends(get_provider_history_projection_reader_factory),
    ],
) -> GetMissionProviderResolutionIncrement:
    return GetMissionProviderResolutionIncrement(
        mission_read_repository_factory,
        waiter,
        projection_reader_factory=projection_reader_factory,
    )


def get_provider_history_projection_verifier(
    mission_repository: Annotated[MissionRepository, Depends(get_mission_repository)],
    projection_reader: Annotated[
        SqlAlchemyProviderHistoryProjectionRepository | None,
        Depends(get_provider_history_projection_reader),
    ],
) -> VerifyMissionProviderHistoryProjection:
    if projection_reader is None:
        raise RuntimeError("Provider history projection is unavailable")
    return VerifyMissionProviderHistoryProjection(mission_repository, projection_reader)

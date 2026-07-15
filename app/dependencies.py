from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db_session
from app.repositories.identity import IdentityRepository
from app.repositories.mission import MissionRepository
from app.repositories.sqlalchemy.identity import SqlAlchemyIdentityRepository
from app.repositories.sqlalchemy.mission import SqlAlchemyMissionRepository
from app.storage.memory import InMemoryIdentityRepository, InMemoryMissionRepository

identity_repository = InMemoryIdentityRepository()
mission_repository = InMemoryMissionRepository()
DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]


def get_identity_repository(session: DbSessionDep) -> IdentityRepository:
    if settings.storage_backend == "database":
        return SqlAlchemyIdentityRepository(session)
    return identity_repository


def get_mission_repository(session: DbSessionDep) -> MissionRepository:
    if settings.storage_backend == "database":
        return SqlAlchemyMissionRepository(session)
    return mission_repository

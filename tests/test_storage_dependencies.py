import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.dependencies import (
    get_identity_repository,
    get_mission_repository,
    identity_repository,
    mission_repository,
)
from app.repositories.sqlalchemy.identity import SqlAlchemyIdentityRepository
from app.repositories.sqlalchemy.mission import SqlAlchemyMissionRepository
from app.storage.memory import InMemoryIdentityRepository, InMemoryMissionRepository


def test_default_storage_backend_is_memory() -> None:
    settings = Settings()

    assert settings.storage_backend == "memory"


def test_memory_backend_returns_in_memory_repositories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.dependencies.settings.storage_backend", "memory")
    session = AsyncSession()

    try:
        resolved_identity_repository = get_identity_repository(session)
        resolved_mission_repository = get_mission_repository(session)
    finally:
        asyncio.run(session.close())

    assert resolved_identity_repository is identity_repository
    assert resolved_mission_repository is mission_repository
    assert isinstance(resolved_identity_repository, InMemoryIdentityRepository)
    assert isinstance(resolved_mission_repository, InMemoryMissionRepository)


def test_database_backend_returns_sqlalchemy_repositories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.dependencies.settings.storage_backend", "database")
    session = AsyncSession()

    try:
        resolved_identity_repository = get_identity_repository(session)
        resolved_mission_repository = get_mission_repository(session)
    finally:
        asyncio.run(session.close())

    assert isinstance(resolved_identity_repository, SqlAlchemyIdentityRepository)
    assert isinstance(resolved_mission_repository, SqlAlchemyMissionRepository)


def test_database_repositories_receive_passed_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.dependencies.settings.storage_backend", "database")
    session = AsyncSession()

    try:
        resolved_identity_repository = get_identity_repository(session)
        resolved_mission_repository = get_mission_repository(session)
    finally:
        asyncio.run(session.close())

    assert resolved_identity_repository._session is session
    assert resolved_mission_repository._session is session

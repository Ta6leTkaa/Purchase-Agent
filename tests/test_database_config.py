import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.session import async_session_maker


def test_settings_contains_database_url() -> None:
    settings = Settings()

    assert settings.database_url


def test_database_url_uses_postgresql_async_driver() -> None:
    settings = Settings()

    assert settings.database_url.startswith("postgresql+asyncpg://")


def test_async_session_factory_creates_session_without_connection() -> None:
    session = async_session_maker()

    assert isinstance(session, AsyncSession)
    asyncio.run(session.close())

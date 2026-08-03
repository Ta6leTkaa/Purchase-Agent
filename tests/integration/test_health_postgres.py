from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.schema import EXPECTED_SCHEMA_REVISION
from app.dependencies import get_storage_session
from app.main import app

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_readiness_accepts_migrated_postgres_schema(
    test_session: AsyncSession,
) -> None:
    async def override_storage_session() -> AsyncIterator[AsyncSession]:
        yield test_session

    app.dependency_overrides[get_storage_session] = override_storage_session
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "storage_backend": "database",
        "schema_revision": EXPECTED_SCHEMA_REVISION,
    }

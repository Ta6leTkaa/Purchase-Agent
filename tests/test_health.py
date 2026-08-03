import ast
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.schema import EXPECTED_SCHEMA_REVISION
from app.dependencies import get_storage_session
from app.main import app


@pytest.fixture(autouse=True)
def clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


def test_health_check() -> None:
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "purchase-agent-api",
    }


def test_readiness_check_reports_memory_backend() -> None:
    client = TestClient(app)

    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "storage_backend": "memory"}


def test_expected_schema_revision_matches_alembic_head() -> None:
    revisions: set[str] = set()
    parent_revisions: set[str] = set()
    for path in Path("alembic/versions").glob("*.py"):
        if path.name == "__init__.py":
            continue
        assignments: dict[str, object] = {}
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id in {"revision", "down_revision"}
                and node.value is not None
            ):
                assignments[node.target.id] = ast.literal_eval(node.value)
        revision = assignments["revision"]
        down_revision = assignments["down_revision"]
        assert isinstance(revision, str)
        revisions.add(revision)
        if isinstance(down_revision, str):
            parent_revisions.add(down_revision)
        elif isinstance(down_revision, (list, tuple)):
            assert all(isinstance(parent, str) for parent in down_revision)
            parent_revisions.update(down_revision)
        else:
            assert down_revision is None

    assert revisions - parent_revisions == {EXPECTED_SCHEMA_REVISION}


def test_readiness_check_reports_current_database_schema() -> None:
    session = AsyncMock(spec=AsyncSession)
    result = MagicMock()
    result.scalars.return_value.all.return_value = [EXPECTED_SCHEMA_REVISION]
    session.execute.return_value = result
    app.dependency_overrides[get_storage_session] = lambda: session

    response = TestClient(app).get("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "storage_backend": "database",
        "schema_revision": EXPECTED_SCHEMA_REVISION,
    }


@pytest.mark.parametrize("revisions", [[], ["20260802_0024"], ["a", "b"]])
def test_readiness_rejects_outdated_or_split_database_schema(
    revisions: list[str],
) -> None:
    session = AsyncMock(spec=AsyncSession)
    result = MagicMock()
    result.scalars.return_value.all.return_value = revisions
    session.execute.return_value = result
    app.dependency_overrides[get_storage_session] = lambda: session

    response = TestClient(app).get("/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "database_schema_not_ready",
        "message": "Database schema revision does not match the API.",
        "expected_revision": EXPECTED_SCHEMA_REVISION,
        "current_revisions": sorted(revisions),
    }


def test_readiness_rejects_unavailable_database() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.execute.side_effect = RuntimeError("connection failed")
    app.dependency_overrides[get_storage_session] = lambda: session

    response = TestClient(app).get("/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == "Database is not ready"

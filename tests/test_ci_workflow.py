from pathlib import Path


def test_ci_workflow_checks_quality_and_container_build() -> None:
    content = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "ruff check ." in content
    assert "mypy app tests" in content
    assert "pytest -q" in content
    assert "alembic heads" in content
    assert "docker compose config --quiet" in content
    assert "docker build" in content
    assert "integration:" in content
    assert "postgres:16" in content
    assert "alembic upgrade head" in content
    assert "pytest -m integration -q" in content
    assert "EXPECTED_SCHEMA_REVISION" in content
    assert 'container-smoke:' in content
    assert "ENVIRONMENT=production" in content
    assert "purchase-agent:smoke python -m app.cli smoke-api" in content
    assert "http://localhost:8000/openapi.json" in content
    assert "docker rm --force purchase-agent-smoke-api" in content

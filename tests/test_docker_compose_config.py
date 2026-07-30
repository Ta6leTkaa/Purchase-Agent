from pathlib import Path


def test_docker_compose_defines_local_postgres() -> None:
    compose_file = Path("docker-compose.yml")

    content = compose_file.read_text(encoding="utf-8")

    assert "postgres:" in content
    assert "image: postgres:16" in content
    assert "container_name: purchase-agent-postgres" in content
    assert "POSTGRES_DB: purchase_agent" in content
    assert '"5432:5432"' in content
    assert "purchase_agent_postgres_data:/var/lib/postgresql/data" in content
    assert "pg_isready -U purchase_agent -d purchase_agent" in content


def test_docker_compose_defines_opt_in_worker() -> None:
    content = Path("docker-compose.yml").read_text(encoding="utf-8")

    assert "worker:" in content
    assert 'profiles: ["worker"]' in content
    assert "app.cli" in content
    assert "WORKER_POLL_INTERVAL_SECONDS" in content
    assert "WORKER_BATCH_SIZE" in content
    assert "WORKER_CLAIM_TIMEOUT_SECONDS" in content
    assert "condition: service_healthy" in content


def test_docker_compose_defines_api_and_database_migration_gate() -> None:
    content = Path("docker-compose.yml").read_text(encoding="utf-8")

    assert "migrate:" in content
    assert 'command: ["alembic", "upgrade", "head"]' in content
    assert "api:" in content
    assert '"8000:8000"' in content
    assert "service_completed_successfully" in content
    assert "ADMIN_API_KEY" in content
    assert "http://localhost:8000/ready" in content


def test_docker_compose_defines_opt_in_notification_worker() -> None:
    content = Path("docker-compose.yml").read_text(encoding="utf-8")

    assert "notification-worker:" in content
    assert 'profiles: ["notifications"]' in content
    assert "NOTIFICATION_WORKER_POLL_INTERVAL_SECONDS" in content
    assert "NOTIFICATION_WORKER_BATCH_SIZE" in content
    assert "NOTIFICATION_CLAIM_TIMEOUT_SECONDS" in content

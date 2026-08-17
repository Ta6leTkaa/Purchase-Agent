from pathlib import Path


def test_docker_compose_defines_local_postgres() -> None:
    compose_file = Path("docker-compose.yml")

    content = compose_file.read_text(encoding="utf-8")

    assert "postgres:" in content
    assert "image: postgres:16" in content
    assert "POSTGRES_DB: purchase_agent" in content
    assert '"${POSTGRES_PORT:-5432}:5432"' in content
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
    assert "WORKER_INSTANCE_ID" in content
    assert "WORKER_HEARTBEAT_MAX_AGE_SECONDS" in content
    assert "worker-health" in content
    assert "--worker-kind" in content
    assert "condition: service_healthy" in content


def test_docker_compose_defines_api_and_database_migration_gate() -> None:
    content = Path("docker-compose.yml").read_text(encoding="utf-8")

    assert "migrate:" in content
    assert 'command: ["alembic", "upgrade", "head"]' in content
    assert "api:" in content
    assert '"${API_PORT:-8000}:8000"' in content
    assert "service_completed_successfully" in content
    assert "ENVIRONMENT: production" in content
    assert "API_KEY" in content
    assert "ADMIN_API_KEY" in content
    assert "API_DOCS_ENABLED" in content
    assert "CORS_ALLOWED_ORIGINS" in content
    assert "MAX_REQUEST_BODY_BYTES" in content
    assert "REQUEST_TIMEOUT_SECONDS" in content
    assert "API_RATE_LIMIT_ENABLED" in content
    assert "API_RATE_LIMIT_REQUESTS" in content
    assert "API_RATE_LIMIT_WINDOW_SECONDS" in content
    assert "API_RATE_LIMIT_MAX_CLIENTS" in content
    assert "TRAIN_PROVIDER_BASE_URL" in content
    assert "TRAIN_PROVIDER_BEARER_TOKEN" in content
    assert "TRAIN_PROVIDER_TIMEOUT_SECONDS" in content
    assert "AGENT_LLM_ENABLED" in content
    assert "OPENAI_API_KEY" in content
    assert "AGENT_LLM_MODEL" in content
    assert "http://localhost:8000/ready" in content


def test_docker_compose_defines_opt_in_notification_worker() -> None:
    content = Path("docker-compose.yml").read_text(encoding="utf-8")

    assert "notification-worker:" in content
    assert 'profiles: ["notifications"]' in content
    assert "NOTIFICATION_WORKER_POLL_INTERVAL_SECONDS" in content
    assert "NOTIFICATION_WORKER_BATCH_SIZE" in content
    assert "NOTIFICATION_CLAIM_TIMEOUT_SECONDS" in content
    assert "NOTIFICATION_RETRY_INITIAL_SECONDS" in content
    assert "NOTIFICATION_RETRY_MAX_SECONDS" in content
    assert "NOTIFICATION_MAX_DELIVERY_ATTEMPTS" in content
    assert "notification-worker-1" in content

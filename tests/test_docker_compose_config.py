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

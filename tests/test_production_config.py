import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings

CLIENT_KEY = "client-key-with-at-least-32-characters"
ADMIN_KEY = "admin-key-with-at-least-32-characters!"


def production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "production",
        "storage_backend": "database",
        "api_key": SecretStr(CLIENT_KEY),
        "admin_api_key": SecretStr(ADMIN_KEY),
        "api_docs_enabled": False,
        "api_rate_limit_enabled": True,
        "APP_DEBUG": False,
    }
    values.update(overrides)
    return Settings.model_validate(values)


def test_safe_production_configuration_is_accepted() -> None:
    configured = production_settings()

    assert configured.environment == "production"
    assert configured.storage_backend == "database"
    assert not configured.debug
    assert not configured.api_docs_enabled
    assert configured.api_rate_limit_enabled


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"storage_backend": "memory"}, "STORAGE_BACKEND must be database"),
        ({"APP_DEBUG": True}, "APP_DEBUG must be false"),
        ({"api_docs_enabled": True}, "API_DOCS_ENABLED must be false"),
        (
            {"api_rate_limit_enabled": False},
            "API_RATE_LIMIT_ENABLED must be true",
        ),
        ({"api_key": None}, "API_KEY must contain at least 32 characters"),
        (
            {"admin_api_key": SecretStr("short")},
            "ADMIN_API_KEY must contain at least 32 characters",
        ),
        (
            {"admin_api_key": SecretStr(CLIENT_KEY)},
            "API_KEY and ADMIN_API_KEY must be different",
        ),
    ],
)
def test_unsafe_production_configuration_is_rejected(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        production_settings(**overrides)


def test_local_environment_keeps_development_defaults() -> None:
    configured = Settings(environment="local")

    assert configured.storage_backend == "memory"
    assert configured.api_key is None
    assert configured.admin_api_key is None
    assert configured.api_docs_enabled
    assert not configured.api_rate_limit_enabled
    assert not configured.agent_llm_enabled
    assert configured.agent_llm_model == "gpt-5.6-terra"


def test_enabled_llm_requires_openai_key_in_production() -> None:
    with pytest.raises(ValidationError, match="OPENAI_API_KEY is required"):
        production_settings(agent_llm_enabled=True)

    configured = production_settings(
        agent_llm_enabled=True,
        openai_api_key=SecretStr("test-openai-key"),
    )
    assert configured.agent_llm_enabled


def test_unknown_environment_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"environment": "staging"})

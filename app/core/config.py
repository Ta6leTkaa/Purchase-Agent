import socket
from typing import Literal

from pydantic import (
    AnyHttpUrl,
    Field,
    SecretStr,
    TypeAdapter,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore",
    )

    app_name: str = "Purchase Agent API"
    environment: Literal["local", "test", "production"] = "local"
    debug: bool = Field(default=False, validation_alias="APP_DEBUG")
    storage_backend: Literal["memory", "database"] = "memory"
    database_url: str = (
        "postgresql+asyncpg://purchase_agent:purchase_agent@localhost:5432/"
        "purchase_agent"
    )
    api_key: SecretStr | None = None
    admin_api_key: SecretStr | None = None
    api_docs_enabled: bool = True
    cors_allowed_origins: list[str] = Field(default_factory=list)
    max_request_body_bytes: int = Field(
        default=1_048_576,
        ge=1_024,
        le=100 * 1_048_576,
    )
    request_timeout_seconds: float = Field(default=60.0, gt=30, le=900)
    api_rate_limit_enabled: bool = False
    api_rate_limit_requests: int = Field(default=120, ge=1, le=100_000)
    api_rate_limit_window_seconds: float = Field(default=60.0, gt=0, le=3600)
    api_rate_limit_max_clients: int = Field(default=10_000, ge=1, le=1_000_000)
    train_provider_base_url: str | None = None
    train_provider_bearer_token: SecretStr | None = None
    train_provider_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    browser_automation_enabled: bool = True
    browser_navigation_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    browser_cdp_url: str | None = None
    agent_llm_enabled: bool = False
    agent_llm_provider: Literal["openai", "ollama"] = "openai"
    openai_api_key: SecretStr | None = None
    agent_llm_model: str = Field(default="gpt-5.6-terra", min_length=1, max_length=100)
    agent_llm_reasoning_effort: Literal["low", "medium", "high"] = "medium"
    agent_llm_timeout_seconds: float = Field(default=45.0, gt=0, le=120)
    agent_llm_max_steps: int = Field(default=12, ge=1, le=50)
    ollama_base_url: str = "http://host.docker.internal:11434"
    ollama_model: str = Field(default="qwen3-vl:4b", min_length=1, max_length=100)
    ollama_fast_model: str | None = Field(
        default="qwen3-vl:2b",
        min_length=1,
        max_length=100,
    )
    ollama_context_window: int = Field(default=32768, ge=4096, le=131072)
    worker_poll_interval_seconds: float = Field(default=5.0, gt=0, le=3600)
    worker_batch_size: int = Field(default=100, ge=1, le=500)
    worker_claim_timeout_seconds: int = Field(default=900, ge=1, le=86400)
    worker_instance_id: str = Field(
        default_factory=socket.gethostname,
        min_length=1,
        max_length=255,
    )
    worker_heartbeat_max_age_seconds: int = Field(default=60, ge=5, le=3600)
    notification_worker_poll_interval_seconds: float = Field(
        default=5.0,
        gt=0,
        le=3600,
    )
    notification_worker_batch_size: int = Field(default=100, ge=1, le=500)
    notification_claim_timeout_seconds: int = Field(
        default=300,
        ge=1,
        le=86400,
    )
    notification_retry_initial_seconds: int = Field(
        default=30,
        ge=1,
        le=86400,
    )
    notification_retry_max_seconds: int = Field(
        default=900,
        ge=1,
        le=86400,
    )
    notification_max_delivery_attempts: int = Field(
        default=5,
        ge=1,
        le=100,
    )
    notification_webhook_url: str | None = None
    notification_webhook_bearer_token: SecretStr | None = None
    notification_webhook_signing_secret: SecretStr | None = None
    notification_webhook_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        le=120,
    )

    @field_validator("cors_allowed_origins")
    @classmethod
    def validate_cors_allowed_origins(cls, values: list[str]) -> list[str]:
        normalized_origins: list[str] = []
        for value in values:
            normalized = value.strip().rstrip("/")
            if not normalized or normalized == "*":
                raise ValueError("cors_allowed_origins must not contain wildcards")
            parsed = TypeAdapter(AnyHttpUrl).validate_python(normalized)
            if (
                parsed.path not in {None, "", "/"}
                or parsed.query is not None
                or parsed.fragment is not None
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise ValueError("CORS origins must not contain paths or credentials")
            if parsed.scheme != "https" and parsed.host not in {
                "localhost",
                "127.0.0.1",
                "::1",
                "[::1]",
            }:
                raise ValueError("CORS origins must use HTTPS for non-local hosts")
            if normalized in normalized_origins:
                raise ValueError("cors_allowed_origins must not contain duplicates")
            normalized_origins.append(normalized)
        return normalized_origins

    @field_validator("notification_webhook_url", mode="before")
    @classmethod
    def validate_notification_webhook_url(
        cls,
        value: object,
    ) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("notification_webhook_url must be a string")
        normalized = value.strip()
        if not normalized:
            return None
        parsed = TypeAdapter(AnyHttpUrl).validate_python(normalized)
        if parsed.scheme != "https" and parsed.host not in {
            "localhost",
            "127.0.0.1",
            "::1",
            "[::1]",
        }:
            raise ValueError(
                "notification_webhook_url must use HTTPS for non-local hosts"
            )
        if parsed.fragment is not None:
            raise ValueError("notification_webhook_url must not contain a fragment")
        return normalized

    @field_validator("train_provider_base_url", mode="before")
    @classmethod
    def validate_train_provider_base_url(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("train_provider_base_url must be a string")
        normalized = value.strip().rstrip("/")
        if not normalized:
            return None
        parsed = TypeAdapter(AnyHttpUrl).validate_python(normalized)
        if parsed.scheme != "https" and parsed.host not in {
            "localhost",
            "127.0.0.1",
            "::1",
            "[::1]",
        }:
            raise ValueError(
                "train_provider_base_url must use HTTPS for non-local hosts"
            )
        if (
            parsed.query is not None
            or parsed.fragment is not None
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError(
                "train_provider_base_url must not contain credentials, query, "
                "or fragment"
            )
        return normalized

    @field_validator("browser_cdp_url", mode="before")
    @classmethod
    def validate_browser_cdp_url(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("browser_cdp_url must be a string")
        normalized = value.strip().rstrip("/")
        if not normalized:
            return None
        parsed = TypeAdapter(AnyHttpUrl).validate_python(normalized)
        if parsed.host not in {"localhost", "127.0.0.1", "host.docker.internal"}:
            raise ValueError("browser_cdp_url must point to a local browser")
        if (
            parsed.path not in {None, "", "/"}
            or parsed.query is not None
            or parsed.fragment is not None
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("browser_cdp_url must not contain a path or credentials")
        return normalized

    @field_validator("openai_api_key", mode="before")
    @classmethod
    def normalize_openai_api_key(cls, value: object) -> object | None:
        if value is None:
            return None
        if isinstance(value, SecretStr):
            return value if value.get_secret_value().strip() else None
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("ollama_base_url", mode="before")
    @classmethod
    def validate_ollama_base_url(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("ollama_base_url must be a string")
        normalized = value.strip().rstrip("/")
        parsed = TypeAdapter(AnyHttpUrl).validate_python(normalized)
        if parsed.host not in {"localhost", "127.0.0.1", "host.docker.internal"}:
            raise ValueError("ollama_base_url must point to a local service")
        if parsed.query is not None or parsed.fragment is not None:
            raise ValueError("ollama_base_url must not contain query or fragment")
        return normalized

    @field_validator("ollama_fast_model", mode="before")
    @classmethod
    def normalize_optional_model(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("worker_instance_id")
    @classmethod
    def normalize_worker_instance_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("worker_instance_id must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_notification_retry_delays(self) -> "Settings":
        if (
            self.notification_retry_max_seconds
            < self.notification_retry_initial_seconds
        ):
            raise ValueError(
                "notification_retry_max_seconds must not be less than "
                "notification_retry_initial_seconds"
            )
        if self.worker_heartbeat_max_age_seconds <= max(
            self.worker_poll_interval_seconds,
            self.notification_worker_poll_interval_seconds,
        ):
            raise ValueError(
                "worker_heartbeat_max_age_seconds must exceed all worker poll intervals"
            )
        return self

    @model_validator(mode="after")
    def validate_production_safety(self) -> "Settings":
        if self.environment != "production":
            return self
        violations: list[str] = []
        if self.storage_backend != "database":
            violations.append("STORAGE_BACKEND must be database")
        if self.debug:
            violations.append("APP_DEBUG must be false")
        if self.api_docs_enabled:
            violations.append("API_DOCS_ENABLED must be false")
        if not self.api_rate_limit_enabled:
            violations.append("API_RATE_LIMIT_ENABLED must be true")
        client_key = self.api_key.get_secret_value() if self.api_key is not None else ""
        admin_key = (
            self.admin_api_key.get_secret_value()
            if self.admin_api_key is not None
            else ""
        )
        if len(client_key) < 32:
            violations.append("API_KEY must contain at least 32 characters")
        if len(admin_key) < 32:
            violations.append("ADMIN_API_KEY must contain at least 32 characters")
        if client_key and admin_key and client_key == admin_key:
            violations.append("API_KEY and ADMIN_API_KEY must be different")
        if (
            self.agent_llm_enabled
            and self.agent_llm_provider == "openai"
            and self.openai_api_key is None
        ):
            violations.append("OPENAI_API_KEY is required when AGENT_LLM_ENABLED=true")
        if violations:
            raise ValueError(
                "Unsafe production configuration: " + "; ".join(violations)
            )
        return self


settings = Settings()

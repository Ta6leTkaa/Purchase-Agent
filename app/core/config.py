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
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Purchase Agent API"
    environment: str = "local"
    debug: bool = Field(default=False, validation_alias="APP_DEBUG")
    storage_backend: Literal["memory", "database"] = "memory"
    database_url: str = (
        "postgresql+asyncpg://purchase_agent:purchase_agent@localhost:5432/"
        "purchase_agent"
    )
    api_key: SecretStr | None = None
    admin_api_key: SecretStr | None = None
    cors_allowed_origins: list[str] = Field(default_factory=list)
    max_request_body_bytes: int = Field(
        default=1_048_576,
        ge=1_024,
        le=100 * 1_048_576,
    )
    request_timeout_seconds: float = Field(default=60.0, gt=30, le=900)
    worker_poll_interval_seconds: float = Field(default=5.0, gt=0, le=3600)
    worker_batch_size: int = Field(default=100, ge=1, le=500)
    worker_claim_timeout_seconds: int = Field(default=900, ge=1, le=86400)
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
                raise ValueError(
                    "CORS origins must use HTTPS for non-local hosts"
                )
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
        return self


settings = Settings()

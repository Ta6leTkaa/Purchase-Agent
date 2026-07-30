from typing import Literal

from pydantic import Field, SecretStr
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
    admin_api_key: SecretStr | None = None
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
    notification_webhook_url: str | None = None
    notification_webhook_bearer_token: SecretStr | None = None
    notification_webhook_signing_secret: SecretStr | None = None
    notification_webhook_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        le=120,
    )


settings = Settings()

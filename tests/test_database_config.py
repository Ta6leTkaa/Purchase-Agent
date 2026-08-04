import asyncio

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.session import async_session_maker


def test_settings_contains_database_url() -> None:
    settings = Settings()

    assert settings.database_url
    assert settings.api_docs_enabled
    assert settings.worker_poll_interval_seconds == 5
    assert settings.worker_batch_size == 100
    assert settings.worker_claim_timeout_seconds == 900
    assert settings.notification_worker_poll_interval_seconds == 5
    assert settings.notification_worker_batch_size == 100
    assert settings.notification_claim_timeout_seconds == 300
    assert settings.notification_retry_initial_seconds == 30
    assert settings.notification_retry_max_seconds == 900
    assert settings.notification_max_delivery_attempts == 5
    assert settings.max_request_body_bytes == 1_048_576
    assert settings.request_timeout_seconds == 60


@pytest.mark.parametrize("value", ["1023", "104857601"])
def test_request_body_limit_rejects_unsafe_bounds(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", value)

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize("value", ["30", "901"])
def test_request_timeout_rejects_unsafe_bounds(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("REQUEST_TIMEOUT_SECONDS", value)

    with pytest.raises(ValidationError):
        Settings()


def test_notification_retry_maximum_must_cover_initial_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOTIFICATION_RETRY_INITIAL_SECONDS", "60")
    monkeypatch.setenv("NOTIFICATION_RETRY_MAX_SECONDS", "30")

    with pytest.raises(
        ValidationError,
        match="notification_retry_max_seconds must not be less than",
    ):
        Settings()


def test_worker_heartbeat_age_must_exceed_poll_intervals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKER_POLL_INTERVAL_SECONDS", "60")
    monkeypatch.setenv("WORKER_HEARTBEAT_MAX_AGE_SECONDS", "60")

    with pytest.raises(ValidationError, match="must exceed all worker poll"):
        Settings()


def test_notification_retry_settings_load_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOTIFICATION_RETRY_INITIAL_SECONDS", "45")
    monkeypatch.setenv("NOTIFICATION_RETRY_MAX_SECONDS", "600")
    monkeypatch.setenv("NOTIFICATION_MAX_DELIVERY_ATTEMPTS", "8")

    configured = Settings()

    assert configured.notification_retry_initial_seconds == 45
    assert configured.notification_retry_max_seconds == 600
    assert configured.notification_max_delivery_attempts == 8


def test_blank_notification_webhook_url_disables_webhook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOTIFICATION_WEBHOOK_URL", "   ")

    assert Settings().notification_webhook_url is None


def test_external_train_provider_defaults_to_disabled() -> None:
    configured = Settings()

    assert configured.train_provider_base_url is None
    assert configured.train_provider_bearer_token is None
    assert configured.train_provider_timeout_seconds == 15


def test_external_train_provider_normalizes_secure_url() -> None:
    configured = Settings(
        train_provider_base_url="  https://trains.example.test/api/  "
    )

    assert configured.train_provider_base_url == "https://trains.example.test/api"


@pytest.mark.parametrize(
    "url",
    [
        "http://trains.example.test",
        "https://trains.example.test?secret=value",
        "https://trains.example.test#fragment",
        "https://user:password@trains.example.test",
    ],
)
def test_external_train_provider_rejects_unsafe_url(url: str) -> None:
    with pytest.raises(ValidationError):
        Settings(train_provider_base_url=url)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:9000/notifications",
        "http://127.0.0.1:9000/notifications",
        "http://[::1]:9000/notifications",
        "https://notifications.example.test/events?tenant=demo",
    ],
)
def test_notification_webhook_url_accepts_safe_urls(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
) -> None:
    monkeypatch.setenv("NOTIFICATION_WEBHOOK_URL", f"  {url}  ")

    assert Settings().notification_webhook_url == url


@pytest.mark.parametrize(
    "url",
    [
        "notifications.example.test/events",
        "ftp://notifications.example.test/events",
        "http://notifications.example.test/events",
        "https://notifications.example.test/events#ignored",
    ],
)
def test_notification_webhook_url_rejects_unsafe_urls(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
) -> None:
    monkeypatch.setenv("NOTIFICATION_WEBHOOK_URL", url)

    with pytest.raises(ValidationError):
        Settings()


def test_database_url_uses_postgresql_async_driver() -> None:
    settings = Settings()

    assert settings.database_url.startswith("postgresql+asyncpg://")


def test_async_session_factory_creates_session_without_connection() -> None:
    session = async_session_maker()

    assert isinstance(session, AsyncSession)
    asyncio.run(session.close())

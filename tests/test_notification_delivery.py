import asyncio
import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx

from app.domain.identity import Identity, NotificationChannel, Preferences
from app.domain.notification import NotificationOutboxMessage
from app.services.notification_delivery import (
    RecipientRoutingNotificationAdapter,
    WebhookNotificationAdapter,
)
from app.services.notification_outbox import NotificationDeliveryError
from app.storage.memory import InMemoryIdentityRepository


def test_webhook_adapter_posts_event_with_idempotency_key() -> None:
    message = NotificationOutboxMessage(
        id=uuid4(),
        mission_id=uuid4(),
        event_id=uuid4(),
        event_type="mission_completed",
        occurred_at=datetime(2026, 7, 30, 10, 0, tzinfo=UTC),
        payload={"type": "mission_completed"},
        available_at=datetime(2026, 7, 30, 10, 0, tzinfo=UTC),
    )
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers["Authorization"]
        captured["idempotency_key"] = request.headers["Idempotency-Key"]
        captured["signature"] = request.headers["X-Purchase-Agent-Signature"]
        captured["body"] = request.content.decode()
        return httpx.Response(204, request=request)

    async def deliver() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await WebhookNotificationAdapter(
                client,
                "https://notify.example.test/events",
                "secret",
                "signing-secret",
            ).deliver(message)

    asyncio.run(deliver())

    assert captured["authorization"] == "Bearer secret"
    assert captured["idempotency_key"] == str(message.event_id)
    assert '"event_type":"mission_completed"' in captured["body"]
    expected_signature = hmac.new(
        b"signing-secret",
        captured["body"].encode(),
        hashlib.sha256,
    ).hexdigest()
    assert captured["signature"] == f"sha256={expected_signature}"


def test_webhook_adapter_raises_for_non_success_response() -> None:
    message = NotificationOutboxMessage(
        id=uuid4(),
        mission_id=uuid4(),
        event_id=uuid4(),
        event_type="mission_failed",
        occurred_at=datetime(2026, 7, 30, 10, 0, tzinfo=UTC),
        payload={},
        available_at=datetime(2026, 7, 30, 10, 0, tzinfo=UTC),
    )

    async def deliver() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(307, request=request)
            )
        ) as client:
            await WebhookNotificationAdapter(
                client, "https://notify.example.test/events"
            ).deliver(message)

    try:
        asyncio.run(deliver())
    except NotificationDeliveryError as exc:
        assert str(exc) == "Webhook returned HTTP 307."
    else:
        raise AssertionError("Expected NotificationDeliveryError")


def test_webhook_adapter_reads_retry_after_header() -> None:
    message = NotificationOutboxMessage(
        id=uuid4(),
        mission_id=uuid4(),
        event_id=uuid4(),
        event_type="mission_failed",
        occurred_at=datetime(2026, 7, 30, 10, 0, tzinfo=UTC),
        payload={},
        available_at=datetime(2026, 7, 30, 10, 0, tzinfo=UTC),
    )

    async def deliver() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    429,
                    headers={"Retry-After": "90"},
                    request=request,
                )
            )
        ) as client:
            await WebhookNotificationAdapter(
                client, "https://notify.example.test/events"
            ).deliver(message)

    try:
        asyncio.run(deliver())
    except NotificationDeliveryError as exc:
        assert exc.retry_delay == timedelta(seconds=90)
    else:
        raise AssertionError("Expected NotificationDeliveryError")


def test_recipient_routing_excludes_disabled_identities() -> None:
    enabled = Identity(
        id=uuid4(), display_name="Enabled", first_name="A", last_name="B",
        birth_date=datetime(1990, 1, 1, tzinfo=UTC).date(),
        preferences=Preferences.model_validate({"notifications": {
            "channels": [NotificationChannel.telegram],
            "external_recipient_id": "chat:42",
        }}),
    )
    disabled = enabled.model_copy(
        update={"id": uuid4(), "preferences": Preferences.model_validate({
            "notifications": {"enabled": False, "external_recipient_id": "chat:7"}
        })}
    )
    message = NotificationOutboxMessage(
        id=uuid4(),
        mission_id=uuid4(),
        event_id=uuid4(),
        event_type="mission_completed",
        occurred_at=datetime(2026, 7, 30, 10, 0, tzinfo=UTC), payload={},
        recipient_ids=[enabled.id, disabled.id],
        available_at=datetime(2026, 7, 30, 10, 0, tzinfo=UTC),
    )

    class CapturingAdapter:
        delivered: NotificationOutboxMessage | None = None

        async def deliver(self, item: NotificationOutboxMessage) -> None:
            self.delivered = item

    async def deliver() -> CapturingAdapter:
        repository = InMemoryIdentityRepository()
        await repository.create(enabled)
        await repository.create(disabled)
        adapter = CapturingAdapter()
        await RecipientRoutingNotificationAdapter(adapter, repository).deliver(message)
        return adapter

    adapter = asyncio.run(deliver())
    assert adapter.delivered is not None
    assert adapter.delivered.payload["recipients"] == [{
        "identity_id": str(enabled.id), "channels": ["telegram"],
        "external_recipient_id": "chat:42",
    }]

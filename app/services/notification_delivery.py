import hashlib
import hmac
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import TextIO

import httpx

from app.domain.notification import NotificationOutboxMessage
from app.repositories.identity import IdentityRepository
from app.services.notification_outbox import (
    NotificationDeliveryAdapter,
    NotificationDeliveryError,
)


class JsonLineNotificationAdapter(NotificationDeliveryAdapter):
    """Development adapter that exposes deliveries as JSON Lines."""

    def __init__(self, output: TextIO) -> None:
        self._output = output

    async def deliver(self, message: NotificationOutboxMessage) -> None:
        self._output.write(message.model_dump_json() + "\n")
        self._output.flush()


class WebhookNotificationAdapter(NotificationDeliveryAdapter):
    """POST each outbox message to a user-controlled HTTPS endpoint."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        url: str,
        bearer_token: str | None = None,
        signing_secret: str | None = None,
    ) -> None:
        self._client = client
        self._url = url
        self._bearer_token = bearer_token
        self._signing_secret = signing_secret

    async def deliver(self, message: NotificationOutboxMessage) -> None:
        body = message.model_dump_json()
        headers = {
            "Content-Type": "application/json",
            "Idempotency-Key": str(message.event_id),
            "X-Purchase-Agent-Delivery-Version": "1",
        }
        if self._bearer_token is not None:
            headers["Authorization"] = f"Bearer {self._bearer_token}"
        if self._signing_secret is not None:
            signature = hmac.new(
                self._signing_secret.encode(),
                body.encode(),
                hashlib.sha256,
            ).hexdigest()
            headers["X-Purchase-Agent-Signature"] = f"sha256={signature}"
        response = await self._client.post(
            self._url,
            content=body,
            headers=headers,
        )
        if not response.is_success:
            raise NotificationDeliveryError(
                f"Webhook returned HTTP {response.status_code}.",
                retry_delay=_retry_after(response),
            )


class RecipientRoutingNotificationAdapter(NotificationDeliveryAdapter):
    """Enrich a delivery with the current, non-sensitive recipient routing data."""

    def __init__(
        self,
        adapter: NotificationDeliveryAdapter,
        identity_repository: IdentityRepository,
    ) -> None:
        self._adapter = adapter
        self._identity_repository = identity_repository

    async def deliver(self, message: NotificationOutboxMessage) -> None:
        recipients: list[dict[str, object]] = []
        for identity_id in message.recipient_ids:
            identity = await self._identity_repository.get(identity_id)
            if identity is None:
                continue
            preferences = identity.preferences.notifications
            if not preferences.enabled or preferences.external_recipient_id is None:
                continue
            recipients.append(
                {
                    "identity_id": str(identity.id),
                    "channels": sorted(
                        channel.value for channel in preferences.channels
                    ),
                    "external_recipient_id": preferences.external_recipient_id,
                }
            )
        if not recipients:
            return
        await self._adapter.deliver(
            message.model_copy(
                update={
                    "payload": {
                        **message.payload,
                        "recipients": recipients,
                    }
                }
            )
        )


@asynccontextmanager
async def open_notification_delivery_adapter(
    output: TextIO,
    *,
    webhook_url: str | None,
    webhook_bearer_token: str | None,
    webhook_signing_secret: str | None,
    webhook_timeout_seconds: float,
) -> AsyncIterator[NotificationDeliveryAdapter]:
    """Use webhook delivery when configured, otherwise retain JSONL behavior."""
    if webhook_url is None:
        yield JsonLineNotificationAdapter(output)
        return
    async with httpx.AsyncClient(timeout=webhook_timeout_seconds) as client:
        yield WebhookNotificationAdapter(
            client,
            webhook_url,
            webhook_bearer_token,
            webhook_signing_secret,
        )


def _retry_after(response: httpx.Response) -> timedelta | None:
    """Accept delta-seconds only; date values depend on receiver clock skew."""
    retry_after = response.headers.get("Retry-After")
    if retry_after is None:
        return None
    try:
        seconds = int(retry_after)
    except ValueError:
        return None
    if seconds <= 0:
        return None
    return timedelta(seconds=min(seconds, 86400))

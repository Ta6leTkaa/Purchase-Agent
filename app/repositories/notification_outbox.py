from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from app.domain.notification import (
    NotificationOutboxMessage,
    NotificationOutboxStatistics,
)


class NotificationOutboxRepository(Protocol):
    async def get_statistics(
        self,
        current_time: datetime,
    ) -> NotificationOutboxStatistics:
        ...

    async def get_message(
        self,
        message_id: UUID,
    ) -> NotificationOutboxMessage | None:
        ...

    async def list_messages(
        self,
        *,
        status: str | None = None,
        mission_id: UUID | None = None,
        limit: int = 100,
    ) -> list[NotificationOutboxMessage]:
        ...

    async def requeue_failed(
        self,
        message_id: UUID,
        current_time: datetime,
    ) -> NotificationOutboxMessage | None:
        ...

    async def claim_pending(
        self,
        current_time: datetime,
        limit: int = 100,
    ) -> list[NotificationOutboxMessage]:
        ...

    async def mark_delivered(
        self,
        message_id: UUID,
        delivered_at: datetime,
    ) -> None:
        ...

    async def recover_stale_claims(
        self,
        current_time: datetime,
        claim_timeout: timedelta,
        limit: int = 100,
    ) -> list[NotificationOutboxMessage]:
        ...

    async def mark_delivery_failed(
        self,
        message_id: UUID,
        *,
        failed_at: datetime,
        retry_at: datetime,
        error: str,
        max_attempts: int,
    ) -> None:
        ...

import asyncio
from datetime import datetime, timedelta
from typing import Protocol

from pydantic import BaseModel

from app.domain.notification import NotificationOutboxMessage
from app.repositories.notification_outbox import NotificationOutboxRepository

NOTIFICATION_EVENT_TYPES = frozenset(
    {
        "mission_cancelled",
        "mission_completed",
        "mission_expired",
        "mission_failed",
        "waiting_for_user_confirmation",
    }
)


class NotificationDeliveryAdapter(Protocol):
    async def deliver(self, message: NotificationOutboxMessage) -> None:
        ...


class NotificationDeliveryError(Exception):
    """Delivery failure that may carry a receiver-selected retry delay."""

    def __init__(
        self,
        message: str,
        *,
        retry_delay: timedelta | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_delay = retry_delay


class NotificationDispatchResult(BaseModel):
    claimed_count: int
    delivered_count: int
    retry_scheduled_count: int
    permanently_failed_count: int


async def dispatch_pending_notifications(
    repository: NotificationOutboxRepository,
    adapter: NotificationDeliveryAdapter,
    current_time: datetime,
    *,
    limit: int = 100,
    retry_delay: timedelta = timedelta(seconds=30),
    max_retry_delay: timedelta = timedelta(minutes=15),
    max_attempts: int = 5,
) -> NotificationDispatchResult:
    if retry_delay <= timedelta(0):
        raise ValueError("retry_delay must be greater than zero")
    if max_retry_delay < retry_delay:
        raise ValueError("max_retry_delay must not be less than retry_delay")
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least one")
    messages = await repository.claim_pending(current_time, limit)
    delivered = 0
    retry_scheduled = 0
    permanently_failed = 0
    for message in messages:
        try:
            await adapter.deliver(message)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            exhausted = message.delivery_attempts >= max_attempts
            requested_retry_delay = (
                exc.retry_delay
                if isinstance(exc, NotificationDeliveryError)
                and exc.retry_delay is not None
                else _exponential_retry_delay(
                    retry_delay,
                    max_retry_delay,
                    message.delivery_attempts,
                )
            )
            await repository.mark_delivery_failed(
                message.id,
                failed_at=current_time,
                retry_at=current_time + requested_retry_delay,
                error=str(exc),
                max_attempts=max_attempts,
            )
            if exhausted:
                permanently_failed += 1
            else:
                retry_scheduled += 1
        else:
            await repository.mark_delivered(message.id, current_time)
            delivered += 1
    return NotificationDispatchResult(
        claimed_count=len(messages),
        delivered_count=delivered,
        retry_scheduled_count=retry_scheduled,
        permanently_failed_count=permanently_failed,
    )


def _exponential_retry_delay(
    base_delay: timedelta,
    max_delay: timedelta,
    attempt: int,
) -> timedelta:
    """Double retry delay after every failed delivery, without overflowing."""
    delay = base_delay
    for _ in range(max(attempt - 1, 0)):
        if delay >= max_delay / 2:
            return max_delay
        delay *= 2
    return min(delay, max_delay)

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.domain.notification import (
    NotificationOutboxMessage,
    NotificationOutboxStatistics,
    NotificationOutboxStatus,
)
from app.services.notification_outbox import (
    NotificationDeliveryError,
    dispatch_pending_notifications,
)

NOW = datetime(2026, 7, 30, 10, 0, tzinfo=UTC)


class FakeOutboxRepository:
    def __init__(self, messages: list[NotificationOutboxMessage]) -> None:
        self.messages = {message.id: message for message in messages}

    async def get_statistics(
        self,
        current_time: datetime,
    ) -> NotificationOutboxStatistics:
        pending = [
            message
            for message in self.messages.values()
            if message.status is NotificationOutboxStatus.pending
        ]
        return NotificationOutboxStatistics(
            pending_count=len(pending),
            processing_count=sum(
                message.status is NotificationOutboxStatus.processing
                for message in self.messages.values()
            ),
            delivered_count=sum(
                message.status is NotificationOutboxStatus.delivered
                for message in self.messages.values()
            ),
            failed_count=sum(
                message.status is NotificationOutboxStatus.failed
                for message in self.messages.values()
            ),
            ready_count=sum(
                message.available_at <= current_time for message in pending
            ),
            oldest_pending_at=min(
                (message.available_at for message in pending),
                default=None,
            ),
        )

    async def get_message(
        self, message_id: UUID
    ) -> NotificationOutboxMessage | None:
        return self.messages.get(message_id)

    async def list_messages(
        self,
        *,
        status: str | None = None,
        mission_id: UUID | None = None,
        limit: int = 100,
    ) -> list[NotificationOutboxMessage]:
        return [
            message
            for message in self.messages.values()
            if (status is None or message.status.value == status)
            and (mission_id is None or message.mission_id == mission_id)
        ][:limit]

    async def requeue_failed(
        self,
        message_id: UUID,
        current_time: datetime,
    ) -> NotificationOutboxMessage | None:
        message = self.messages.get(message_id)
        if message is None or message.status is not NotificationOutboxStatus.failed:
            return message
        message.status = NotificationOutboxStatus.pending
        message.delivery_attempts = 0
        message.available_at = current_time
        message.last_error = None
        return message

    async def claim_pending(
        self,
        current_time: datetime,
        limit: int = 100,
    ) -> list[NotificationOutboxMessage]:
        claimed = [
            message
            for message in self.messages.values()
            if message.status is NotificationOutboxStatus.pending
            and message.available_at <= current_time
        ][:limit]
        for message in claimed:
            message.status = NotificationOutboxStatus.processing
            message.claimed_at = current_time
            message.delivery_attempts += 1
        return claimed

    async def mark_delivered(
        self,
        message_id: UUID,
        delivered_at: datetime,
    ) -> None:
        message = self.messages[message_id]
        message.status = NotificationOutboxStatus.delivered
        message.delivered_at = delivered_at
        message.claimed_at = None

    async def recover_stale_claims(
        self,
        current_time: datetime,
        claim_timeout: timedelta,
        limit: int = 100,
    ) -> list[NotificationOutboxMessage]:
        stale_before = current_time - claim_timeout
        recovered = [
            message
            for message in self.messages.values()
            if message.status is NotificationOutboxStatus.processing
            and message.claimed_at is not None
            and message.claimed_at <= stale_before
        ][:limit]
        for message in recovered:
            message.status = NotificationOutboxStatus.pending
            message.available_at = current_time
            message.claimed_at = None
        return recovered

    async def mark_delivery_failed(
        self,
        message_id: UUID,
        *,
        failed_at: datetime,
        retry_at: datetime,
        error: str,
        max_attempts: int,
    ) -> None:
        del failed_at
        message = self.messages[message_id]
        message.status = (
            NotificationOutboxStatus.failed
            if message.delivery_attempts >= max_attempts
            else NotificationOutboxStatus.pending
        )
        message.available_at = retry_at
        message.claimed_at = None
        message.last_error = error


class CapturingAdapter:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.delivered: list[UUID] = []

    async def deliver(self, message: NotificationOutboxMessage) -> None:
        if self.fail:
            raise ConnectionError("notification channel unavailable")
        self.delivered.append(message.id)


def make_message(*, attempts: int = 0) -> NotificationOutboxMessage:
    return NotificationOutboxMessage(
        id=uuid4(),
        mission_id=uuid4(),
        event_id=uuid4(),
        event_type="mission_completed",
        occurred_at=NOW,
        payload={"type": "mission_completed"},
        delivery_attempts=attempts,
        available_at=NOW,
    )


def test_dispatch_marks_successful_messages_delivered() -> None:
    message = make_message()
    repository = FakeOutboxRepository([message])
    adapter = CapturingAdapter()

    result = asyncio.run(
        dispatch_pending_notifications(
            repository,
            adapter,
            NOW,
        )
    )

    assert result.delivered_count == 1
    assert adapter.delivered == [message.id]
    assert message.status is NotificationOutboxStatus.delivered
    assert message.delivered_at == NOW


def test_dispatch_reschedules_transient_delivery_failure() -> None:
    message = make_message()
    repository = FakeOutboxRepository([message])

    result = asyncio.run(
        dispatch_pending_notifications(
            repository,
            CapturingAdapter(fail=True),
            NOW,
            retry_delay=timedelta(minutes=1),
        )
    )

    assert result.retry_scheduled_count == 1
    assert message.status is NotificationOutboxStatus.pending
    assert message.available_at == NOW + timedelta(minutes=1)
    assert message.last_error == "notification channel unavailable"


@pytest.mark.parametrize(
    ("previous_attempts", "expected_delay"),
    [
        (0, timedelta(seconds=30)),
        (1, timedelta(minutes=1)),
        (2, timedelta(minutes=2)),
        (10, timedelta(minutes=15)),
    ],
)
def test_dispatch_uses_bounded_exponential_retry_delay(
    previous_attempts: int,
    expected_delay: timedelta,
) -> None:
    message = make_message(attempts=previous_attempts)
    repository = FakeOutboxRepository([message])

    asyncio.run(
        dispatch_pending_notifications(
            repository,
            CapturingAdapter(fail=True),
            NOW,
            max_attempts=20,
        )
    )

    assert message.available_at == NOW + expected_delay


def test_dispatch_permanently_fails_after_attempt_limit() -> None:
    message = make_message(attempts=4)
    repository = FakeOutboxRepository([message])

    result = asyncio.run(
        dispatch_pending_notifications(
            repository,
            CapturingAdapter(fail=True),
            NOW,
            max_attempts=5,
        )
    )

    assert result.permanently_failed_count == 1
    assert message.status is NotificationOutboxStatus.failed


def test_dispatch_immediately_fails_non_retryable_delivery_error() -> None:
    class InvalidRequestAdapter:
        async def deliver(self, message: NotificationOutboxMessage) -> None:
            del message
            raise NotificationDeliveryError(
                "invalid receiver request",
                retryable=False,
            )

    message = make_message()
    repository = FakeOutboxRepository([message])

    result = asyncio.run(
        dispatch_pending_notifications(
            repository,
            InvalidRequestAdapter(),
            NOW,
            max_attempts=5,
        )
    )

    assert result.permanently_failed_count == 1
    assert result.retry_scheduled_count == 0
    assert message.status is NotificationOutboxStatus.failed
    assert message.delivery_attempts == 1


def test_dispatch_honors_adapter_retry_delay() -> None:
    class RetryAfterAdapter:
        async def deliver(self, message: NotificationOutboxMessage) -> None:
            del message
            raise NotificationDeliveryError(
                "rate limited",
                retry_delay=timedelta(minutes=2),
            )

    message = make_message()
    repository = FakeOutboxRepository([message])

    asyncio.run(
        dispatch_pending_notifications(repository, RetryAfterAdapter(), NOW)
    )

    assert message.available_at == NOW + timedelta(minutes=2)


def test_dispatch_rejects_retry_delay_above_maximum() -> None:
    message = make_message()

    with pytest.raises(
        ValueError,
        match="max_retry_delay must not be less than retry_delay",
    ):
        asyncio.run(
            dispatch_pending_notifications(
                FakeOutboxRepository([message]),
                CapturingAdapter(),
                NOW,
                retry_delay=timedelta(minutes=2),
                max_retry_delay=timedelta(minutes=1),
            )
        )

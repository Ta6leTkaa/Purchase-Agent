import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.domain.notification import (
    NotificationOutboxMessage,
    NotificationOutboxStatistics,
    NotificationOutboxStatus,
)
from app.services.notification_worker import process_notification_worker_cycle

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)


class WorkerRepository:
    def __init__(self, message: NotificationOutboxMessage) -> None:
        self.message = message

    async def get_statistics(
        self,
        current_time: datetime,
    ) -> NotificationOutboxStatistics:
        del current_time
        raise NotImplementedError

    async def get_message(
        self, message_id: UUID
    ) -> NotificationOutboxMessage | None:
        return self.message if message_id == self.message.id else None

    async def list_messages(
        self,
        *,
        status: str | None = None,
        mission_id: UUID | None = None,
        limit: int = 100,
    ) -> list[NotificationOutboxMessage]:
        if limit < 1:
            return []
        if status is not None and self.message.status.value != status:
            return []
        if mission_id is not None and self.message.mission_id != mission_id:
            return []
        return [self.message]

    async def requeue_failed(
        self,
        message_id: UUID,
        current_time: datetime,
    ) -> NotificationOutboxMessage | None:
        del current_time
        return await self.get_message(message_id)

    async def recover_stale_claims(
        self,
        current_time: datetime,
        claim_timeout: timedelta,
        limit: int = 100,
    ) -> list[NotificationOutboxMessage]:
        del limit
        if (
            self.message.status is NotificationOutboxStatus.processing
            and self.message.claimed_at is not None
            and self.message.claimed_at <= current_time - claim_timeout
        ):
            self.message.status = NotificationOutboxStatus.pending
            self.message.claimed_at = None
            self.message.available_at = current_time
            return [self.message]
        return []

    async def claim_pending(
        self,
        current_time: datetime,
        limit: int = 100,
    ) -> list[NotificationOutboxMessage]:
        del limit
        if (
            self.message.status is NotificationOutboxStatus.pending
            and self.message.available_at <= current_time
        ):
            self.message.status = NotificationOutboxStatus.processing
            self.message.claimed_at = current_time
            self.message.delivery_attempts += 1
            return [self.message]
        return []

    async def mark_delivered(
        self,
        message_id: UUID,
        delivered_at: datetime,
    ) -> None:
        assert message_id == self.message.id
        self.message.status = NotificationOutboxStatus.delivered
        self.message.delivered_at = delivered_at
        self.message.claimed_at = None

    async def mark_delivery_failed(
        self,
        message_id: UUID,
        *,
        failed_at: datetime,
        retry_at: datetime,
        error: str,
        max_attempts: int,
    ) -> None:
        raise AssertionError(
            (
                message_id,
                failed_at,
                retry_at,
                error,
                max_attempts,
            )
        )


class CapturingAdapter:
    def __init__(self) -> None:
        self.ids: list[UUID] = []

    async def deliver(self, message: NotificationOutboxMessage) -> None:
        self.ids.append(message.id)


def test_notification_worker_recovers_and_delivers_stale_claim() -> None:
    message = NotificationOutboxMessage(
        id=uuid4(),
        mission_id=uuid4(),
        event_id=uuid4(),
        event_type="mission_completed",
        occurred_at=NOW - timedelta(minutes=10),
        payload={},
        status=NotificationOutboxStatus.processing,
        delivery_attempts=1,
        available_at=NOW - timedelta(minutes=10),
        claimed_at=NOW - timedelta(minutes=6),
    )
    repository = WorkerRepository(message)
    adapter = CapturingAdapter()

    result = asyncio.run(
        process_notification_worker_cycle(
            repository,
            adapter,
            NOW,
            claim_timeout=timedelta(minutes=5),
        )
    )

    assert result.recovered_count == 1
    assert result.dispatch.delivered_count == 1
    assert adapter.ids == [message.id]
    assert message.status is NotificationOutboxStatus.delivered
    assert message.delivery_attempts == 2

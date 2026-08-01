from datetime import datetime, timedelta

from pydantic import BaseModel

from app.repositories.notification_outbox import NotificationOutboxRepository
from app.services.notification_outbox import (
    NotificationDeliveryAdapter,
    NotificationDispatchResult,
    dispatch_pending_notifications,
)


class NotificationWorkerCycleResult(BaseModel):
    recovered_count: int
    dispatch: NotificationDispatchResult


async def process_notification_worker_cycle(
    repository: NotificationOutboxRepository,
    adapter: NotificationDeliveryAdapter,
    current_time: datetime,
    *,
    limit: int = 100,
    claim_timeout: timedelta = timedelta(minutes=5),
    retry_delay: timedelta = timedelta(seconds=30),
    max_retry_delay: timedelta = timedelta(minutes=15),
    max_attempts: int = 5,
) -> NotificationWorkerCycleResult:
    recovered = await repository.recover_stale_claims(
        current_time,
        claim_timeout,
        limit,
    )
    dispatch = await dispatch_pending_notifications(
        repository,
        adapter,
        current_time,
        limit=limit,
        retry_delay=retry_delay,
        max_retry_delay=max_retry_delay,
        max_attempts=max_attempts,
    )
    return NotificationWorkerCycleResult(
        recovered_count=len(recovered),
        dispatch=dispatch,
    )

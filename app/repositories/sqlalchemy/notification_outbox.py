from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.notification_outbox import (
    NotificationOutboxMessageModel,
    notification_outbox_from_model,
)
from app.domain.notification import (
    NotificationOutboxMessage,
    NotificationOutboxStatistics,
    NotificationOutboxStatus,
)
from app.repositories.notification_outbox import NotificationOutboxRepository
from app.services.notification_outbox_pagination import NotificationOutboxCursor


class SqlAlchemyNotificationOutboxRepository(
    NotificationOutboxRepository
):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_statistics(
        self,
        current_time: datetime,
    ) -> NotificationOutboxStatistics:
        _validate_time(current_time)
        status = NotificationOutboxMessageModel.status
        result = await self._session.execute(
            select(
                func.count().filter(
                    status == NotificationOutboxStatus.pending.value
                ),
                func.count().filter(
                    status == NotificationOutboxStatus.processing.value
                ),
                func.count().filter(
                    status == NotificationOutboxStatus.delivered.value
                ),
                func.count().filter(
                    status == NotificationOutboxStatus.failed.value
                ),
                func.count().filter(
                    (status == NotificationOutboxStatus.pending.value)
                    & (
                        NotificationOutboxMessageModel.available_at
                        <= current_time
                    )
                ),
                func.min(
                    case(
                        (
                            status == NotificationOutboxStatus.pending.value,
                            NotificationOutboxMessageModel.available_at,
                        )
                    )
                ),
            )
        )
        row = result.one()
        return NotificationOutboxStatistics(
            pending_count=row[0],
            processing_count=row[1],
            delivered_count=row[2],
            failed_count=row[3],
            ready_count=row[4],
            oldest_pending_at=row[5],
        )

    async def get_message(
        self,
        message_id: UUID,
    ) -> NotificationOutboxMessage | None:
        model = await self._session.get(NotificationOutboxMessageModel, message_id)
        return (
            notification_outbox_from_model(model)
            if model is not None
            else None
        )

    async def list_messages(
        self,
        *,
        status: str | None = None,
        mission_id: UUID | None = None,
        limit: int = 100,
    ) -> list[NotificationOutboxMessage]:
        if limit < 1:
            raise ValueError("limit must be at least one")
        statement = select(NotificationOutboxMessageModel)
        if status is not None:
            statement = statement.where(
                NotificationOutboxMessageModel.status == status
            )
        if mission_id is not None:
            statement = statement.where(
                NotificationOutboxMessageModel.mission_id == mission_id
            )
        result = await self._session.execute(
            statement.order_by(
                NotificationOutboxMessageModel.occurred_at.desc(),
                NotificationOutboxMessageModel.id.desc(),
            ).limit(limit)
        )
        return [
            notification_outbox_from_model(model)
            for model in result.scalars().all()
        ]

    async def list_message_page_candidates(
        self,
        *,
        status: str | None = None,
        mission_id: UUID | None = None,
        cursor: NotificationOutboxCursor | None = None,
        limit: int = 101,
    ) -> list[NotificationOutboxMessage]:
        if limit < 1:
            raise ValueError("limit must be at least one")
        statement = select(NotificationOutboxMessageModel)
        if status is not None:
            statement = statement.where(
                NotificationOutboxMessageModel.status == status
            )
        if mission_id is not None:
            statement = statement.where(
                NotificationOutboxMessageModel.mission_id == mission_id
            )
        if cursor is not None:
            statement = statement.where(
                or_(
                    NotificationOutboxMessageModel.occurred_at
                    < cursor.occurred_at,
                    and_(
                        NotificationOutboxMessageModel.occurred_at
                        == cursor.occurred_at,
                        NotificationOutboxMessageModel.id < cursor.message_id,
                    ),
                )
            )
        result = await self._session.execute(
            statement.order_by(
                NotificationOutboxMessageModel.occurred_at.desc(),
                NotificationOutboxMessageModel.id.desc(),
            ).limit(limit)
        )
        return [
            notification_outbox_from_model(model)
            for model in result.scalars().all()
        ]

    async def requeue_failed(
        self,
        message_id: UUID,
        current_time: datetime,
    ) -> NotificationOutboxMessage | None:
        _validate_time(current_time)
        message = await self._session.get(
            NotificationOutboxMessageModel,
            message_id,
            with_for_update=True,
        )
        if message is None:
            return None
        if message.status != NotificationOutboxStatus.failed.value:
            return notification_outbox_from_model(message)
        message.status = NotificationOutboxStatus.pending.value
        message.delivery_attempts = 0
        message.available_at = current_time
        message.claimed_at = None
        message.delivered_at = None
        message.last_error = None
        await self._session.commit()
        return notification_outbox_from_model(message)

    async def claim_pending(
        self,
        current_time: datetime,
        limit: int = 100,
    ) -> list[NotificationOutboxMessage]:
        _validate_arguments(current_time, limit)
        result = await self._session.execute(
            select(NotificationOutboxMessageModel)
            .where(
                NotificationOutboxMessageModel.status
                == NotificationOutboxStatus.pending.value
            )
            .where(
                NotificationOutboxMessageModel.available_at <= current_time
            )
            .order_by(
                NotificationOutboxMessageModel.available_at.asc(),
                NotificationOutboxMessageModel.occurred_at.asc(),
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        models = list(result.scalars().all())
        for model in models:
            model.status = NotificationOutboxStatus.processing.value
            model.claimed_at = current_time
            model.delivery_attempts += 1
        await self._session.flush()
        await self._session.commit()
        return [notification_outbox_from_model(model) for model in models]

    async def mark_delivered(
        self,
        message_id: UUID,
        delivered_at: datetime,
    ) -> None:
        _validate_time(delivered_at)
        await self._session.execute(
            update(NotificationOutboxMessageModel)
            .where(NotificationOutboxMessageModel.id == message_id)
            .where(
                NotificationOutboxMessageModel.status
                == NotificationOutboxStatus.processing.value
            )
            .values(
                status=NotificationOutboxStatus.delivered.value,
                delivered_at=delivered_at,
                claimed_at=None,
                last_error=None,
            )
        )
        await self._session.commit()

    async def recover_stale_claims(
        self,
        current_time: datetime,
        claim_timeout: timedelta,
        limit: int = 100,
    ) -> list[NotificationOutboxMessage]:
        _validate_arguments(current_time, limit)
        if claim_timeout <= timedelta(0):
            raise ValueError("claim_timeout must be greater than zero")
        stale_before = current_time - claim_timeout
        result = await self._session.execute(
            select(NotificationOutboxMessageModel)
            .where(
                NotificationOutboxMessageModel.status
                == NotificationOutboxStatus.processing.value
            )
            .where(NotificationOutboxMessageModel.claimed_at.is_not(None))
            .where(
                NotificationOutboxMessageModel.claimed_at <= stale_before
            )
            .order_by(NotificationOutboxMessageModel.claimed_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        models = list(result.scalars().all())
        for model in models:
            model.status = NotificationOutboxStatus.pending.value
            model.available_at = current_time
            model.claimed_at = None
            model.last_error = "Delivery claim recovered after timeout."
        await self._session.flush()
        await self._session.commit()
        return [notification_outbox_from_model(model) for model in models]

    async def mark_delivery_failed(
        self,
        message_id: UUID,
        *,
        failed_at: datetime,
        retry_at: datetime,
        error: str,
        max_attempts: int,
    ) -> None:
        _validate_time(failed_at)
        _validate_time(retry_at)
        if retry_at < failed_at:
            raise ValueError("retry_at must not be before failed_at")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        message = await self._session.get(
            NotificationOutboxMessageModel,
            message_id,
            with_for_update=True,
        )
        if (
            message is None
            or message.status
            != NotificationOutboxStatus.processing.value
        ):
            return
        exhausted = message.delivery_attempts >= max_attempts
        message.status = (
            NotificationOutboxStatus.failed.value
            if exhausted
            else NotificationOutboxStatus.pending.value
        )
        message.available_at = retry_at
        message.claimed_at = None
        message.last_error = error[:2000]
        await self._session.commit()


def _validate_arguments(current_time: datetime, limit: int) -> None:
    _validate_time(current_time)
    if limit < 1:
        raise ValueError("limit must be at least one")


def _validate_time(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("time must be timezone-aware")

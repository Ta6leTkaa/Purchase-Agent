from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.admin import (
    RecoverStaleNotificationsRequest,
    recover_stale_notifications_endpoint,
)
from app.db.models.notification_outbox import NotificationOutboxMessageModel
from app.domain.mission import Mission, MissionStatus, TrainConstraints
from app.domain.notification import (
    NotificationOutboxMessage,
    NotificationOutboxStatus,
)
from app.repositories.sqlalchemy.mission import SqlAlchemyMissionRepository
from app.repositories.sqlalchemy.notification_outbox import (
    SqlAlchemyNotificationOutboxRepository,
)
from app.services.notification_outbox import (
    NotificationDeliveryError,
    dispatch_pending_notifications,
)
from app.services.notification_outbox_pagination import NotificationOutboxCursor

pytestmark = pytest.mark.integration
NOW = datetime(2026, 7, 30, 10, 0, tzinfo=UTC)


class CapturingNotificationAdapter:
    def __init__(self) -> None:
        self.messages: list[NotificationOutboxMessage] = []

    async def deliver(self, message: NotificationOutboxMessage) -> None:
        self.messages.append(message)


class NonRetryableNotificationAdapter:
    async def deliver(self, message: NotificationOutboxMessage) -> None:
        del message
        raise NotificationDeliveryError(
            "receiver rejected payload",
            retryable=False,
        )


async def test_notification_outbox_has_operational_indexes(
    test_session: AsyncSession,
) -> None:
    connection = await test_session.connection()
    indexes = await connection.run_sync(
        lambda sync_connection: inspect(sync_connection).get_indexes(
            "notification_outbox"
        )
    )
    indexed_columns = {
        index["name"]: index["column_names"] for index in indexes
    }

    assert indexed_columns["ix_notification_outbox_dispatch"] == [
        "status",
        "available_at",
    ]
    assert indexed_columns["ix_notification_outbox_page"] == [
        "occurred_at",
        "id",
    ]
    assert indexed_columns["ix_notification_outbox_status_page"] == [
        "status",
        "occurred_at",
        "id",
    ]
    assert indexed_columns["ix_notification_outbox_mission_page"] == [
        "mission_id",
        "occurred_at",
        "id",
    ]


async def test_mission_event_creates_and_dispatches_transactional_outbox(
    test_session: AsyncSession,
) -> None:
    mission_repository = SqlAlchemyMissionRepository(test_session)
    mission = Mission(
        id=uuid4(),
        title="Outbox mission",
        participant_ids=[uuid4()],
        provider="mock_train",
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Saint Petersburg",
            travel_date=date(2026, 8, 1),
            passengers_count=1,
        ),
    )
    await mission_repository.create(mission)
    mission.status = MissionStatus.completed
    event = mission.record_event(
        timestamp=NOW,
        event_type="mission_completed",
        message="Mission completed.",
    )
    await mission_repository.update(mission)
    await test_session.commit()

    outbox_count = await test_session.scalar(
        select(func.count()).select_from(NotificationOutboxMessageModel)
    )
    adapter = CapturingNotificationAdapter()
    result = await dispatch_pending_notifications(
        SqlAlchemyNotificationOutboxRepository(test_session),
        adapter,
        NOW,
    )

    assert outbox_count == 1
    assert result.delivered_count == 1
    assert adapter.messages[0].event_id == event.event_id
    assert adapter.messages[0].recipient_ids == mission.participant_ids
    stored = await test_session.get(
        NotificationOutboxMessageModel,
        adapter.messages[0].id,
    )
    assert stored is not None
    assert stored.status == NotificationOutboxStatus.delivered.value
    assert stored.delivered_at == NOW


async def test_non_notification_event_does_not_create_outbox_row(
    test_session: AsyncSession,
) -> None:
    repository = SqlAlchemyMissionRepository(test_session)
    mission = Mission(
        id=uuid4(),
        title="Internal event mission",
        participant_ids=[uuid4()],
        provider="mock_train",
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Saint Petersburg",
            travel_date=date(2026, 8, 1),
            passengers_count=1,
        ),
    )
    await repository.create(mission)
    mission.record_event(
        timestamp=NOW,
        event_type="search_started",
        message="Search started.",
    )
    await repository.update(mission)
    await test_session.commit()

    assert await test_session.scalar(
        select(func.count()).select_from(NotificationOutboxMessageModel)
    ) == 0


async def test_non_retryable_delivery_is_dead_lettered_after_first_attempt(
    test_session: AsyncSession,
) -> None:
    mission = Mission(
        id=uuid4(),
        title="Rejected notification",
        participant_ids=[uuid4()],
        provider="mock_train",
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Saint Petersburg",
            travel_date=date(2026, 8, 1),
            passengers_count=1,
        ),
    )
    await SqlAlchemyMissionRepository(test_session).create(mission)
    message = NotificationOutboxMessageModel(
        id=uuid4(),
        mission_id=mission.id,
        event_id=uuid4(),
        event_type="mission_failed",
        occurred_at=NOW,
        payload={},
        status=NotificationOutboxStatus.pending.value,
        delivery_attempts=0,
        available_at=NOW,
    )
    test_session.add(message)
    await test_session.commit()

    result = await dispatch_pending_notifications(
        SqlAlchemyNotificationOutboxRepository(test_session),
        NonRetryableNotificationAdapter(),
        NOW,
        max_attempts=5,
    )

    await test_session.refresh(message)
    assert result.permanently_failed_count == 1
    assert message.status == NotificationOutboxStatus.failed.value
    assert message.delivery_attempts == 1
    assert message.last_error == "receiver rejected payload"


async def test_stale_delivery_claim_is_recovered(
    test_session: AsyncSession,
) -> None:
    mission_repository = SqlAlchemyMissionRepository(test_session)
    mission = Mission(
        id=uuid4(),
        title="Stale notification claim",
        participant_ids=[uuid4()],
        provider="mock_train",
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Saint Petersburg",
            travel_date=date(2026, 8, 1),
            passengers_count=1,
        ),
    )
    await mission_repository.create(mission)
    message = NotificationOutboxMessageModel(
        id=uuid4(),
        mission_id=mission.id,
        event_id=uuid4(),
        event_type="mission_completed",
        occurred_at=NOW - timedelta(minutes=10),
        payload={},
        status=NotificationOutboxStatus.processing.value,
        delivery_attempts=1,
        available_at=NOW - timedelta(minutes=10),
        claimed_at=NOW - timedelta(minutes=6),
    )
    test_session.add(message)
    await test_session.commit()

    recovered = await SqlAlchemyNotificationOutboxRepository(
        test_session
    ).recover_stale_claims(
        NOW,
        timedelta(minutes=5),
    )

    assert [item.id for item in recovered] == [message.id]
    assert recovered[0].status is NotificationOutboxStatus.pending
    assert recovered[0].claimed_at is None
    assert recovered[0].available_at == NOW


async def test_admin_recovery_reports_recovered_notification_ids(
    test_session: AsyncSession,
) -> None:
    mission = Mission(
        id=uuid4(),
        title="Admin notification recovery",
        participant_ids=[uuid4()],
        provider="mock_train",
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Saint Petersburg",
            travel_date=date(2026, 8, 1),
            passengers_count=1,
        ),
    )
    await SqlAlchemyMissionRepository(test_session).create(mission)
    stale = NotificationOutboxMessageModel(
        id=uuid4(),
        mission_id=mission.id,
        event_id=uuid4(),
        event_type="mission_completed",
        occurred_at=NOW - timedelta(minutes=10),
        payload={},
        status=NotificationOutboxStatus.processing.value,
        delivery_attempts=2,
        available_at=NOW - timedelta(minutes=10),
        claimed_at=NOW - timedelta(minutes=6),
    )
    test_session.add(stale)
    await test_session.commit()

    result = await recover_stale_notifications_endpoint(
        RecoverStaleNotificationsRequest(
            claim_timeout_seconds=300,
            limit=100,
        ),
        None,
        test_session,
        NOW,
    )

    assert result.recovered_count == 1
    assert result.recovered_message_ids == [stale.id]
    await test_session.refresh(stale)
    assert stale.status == NotificationOutboxStatus.pending.value
    assert stale.delivery_attempts == 2
    assert stale.claimed_at is None


async def test_list_filters_messages_and_requeues_failed_delivery(
    test_session: AsyncSession,
) -> None:
    mission_repository = SqlAlchemyMissionRepository(test_session)
    mission = Mission(
        id=uuid4(),
        title="Retry failed notification",
        participant_ids=[uuid4()],
        provider="mock_train",
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Saint Petersburg",
            travel_date=date(2026, 8, 1),
            passengers_count=1,
        ),
    )
    await mission_repository.create(mission)
    failed = NotificationOutboxMessageModel(
        id=uuid4(),
        mission_id=mission.id,
        event_id=uuid4(),
        event_type="mission_failed",
        occurred_at=NOW,
        payload={},
        status=NotificationOutboxStatus.failed.value,
        delivery_attempts=5,
        available_at=NOW - timedelta(minutes=1),
        last_error="channel unavailable",
    )
    delivered = NotificationOutboxMessageModel(
        id=uuid4(),
        mission_id=mission.id,
        event_id=uuid4(),
        event_type="mission_completed",
        occurred_at=NOW - timedelta(minutes=1),
        payload={},
        status=NotificationOutboxStatus.delivered.value,
        delivery_attempts=1,
        available_at=NOW - timedelta(minutes=1),
        delivered_at=NOW,
    )
    test_session.add_all([failed, delivered])
    await test_session.commit()

    repository = SqlAlchemyNotificationOutboxRepository(test_session)
    listed = await repository.list_messages(
        status=NotificationOutboxStatus.failed.value,
        mission_id=mission.id,
    )
    requeued = await repository.requeue_failed(failed.id, NOW)

    assert [item.id for item in listed] == [failed.id]
    assert requeued is not None
    assert requeued.status is NotificationOutboxStatus.pending
    assert requeued.delivery_attempts == 0
    assert requeued.available_at == NOW
    assert requeued.last_error is None


async def test_outbox_statistics_summarize_backlog(
    test_session: AsyncSession,
) -> None:
    mission = Mission(
        id=uuid4(),
        title="Notification backlog",
        participant_ids=[uuid4()],
        provider="mock_train",
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Saint Petersburg",
            travel_date=date(2026, 8, 1),
            passengers_count=1,
        ),
    )
    await SqlAlchemyMissionRepository(test_session).create(mission)
    messages = [
        NotificationOutboxMessageModel(
            id=uuid4(),
            mission_id=mission.id,
            event_id=uuid4(),
            event_type="mission_completed",
            occurred_at=NOW - timedelta(minutes=index),
            payload={},
            status=status.value,
            delivery_attempts=0,
            available_at=available_at,
        )
        for index, (status, available_at) in enumerate(
            [
                (
                    NotificationOutboxStatus.pending,
                    NOW - timedelta(minutes=5),
                ),
                (
                    NotificationOutboxStatus.pending,
                    NOW + timedelta(minutes=5),
                ),
                (NotificationOutboxStatus.processing, NOW),
                (NotificationOutboxStatus.delivered, NOW),
                (NotificationOutboxStatus.failed, NOW),
            ]
        )
    ]
    test_session.add_all(messages)
    await test_session.commit()

    statistics = await SqlAlchemyNotificationOutboxRepository(
        test_session
    ).get_statistics(NOW)

    assert statistics.pending_count == 2
    assert statistics.processing_count == 1
    assert statistics.delivered_count == 1
    assert statistics.failed_count == 1
    assert statistics.ready_count == 1
    assert statistics.oldest_pending_at == NOW - timedelta(minutes=5)


async def test_outbox_cursor_pagination_is_stable_for_equal_timestamps(
    test_session: AsyncSession,
) -> None:
    mission = Mission(
        id=uuid4(),
        title="Paginated notification backlog",
        participant_ids=[uuid4()],
        provider="mock_train",
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Saint Petersburg",
            travel_date=date(2026, 8, 1),
            passengers_count=1,
        ),
    )
    await SqlAlchemyMissionRepository(test_session).create(mission)
    ids = sorted([uuid4(), uuid4(), uuid4()], reverse=True)
    test_session.add_all(
        [
            NotificationOutboxMessageModel(
                id=message_id,
                mission_id=mission.id,
                event_id=uuid4(),
                event_type="mission_completed",
                occurred_at=NOW,
                payload={},
                status=NotificationOutboxStatus.pending.value,
                delivery_attempts=0,
                available_at=NOW,
            )
            for message_id in ids
        ]
    )
    await test_session.commit()
    repository = SqlAlchemyNotificationOutboxRepository(test_session)

    first = await repository.list_message_page_candidates(limit=3)
    second = await repository.list_message_page_candidates(
        cursor=NotificationOutboxCursor(
            occurred_at=first[1].occurred_at,
            message_id=first[1].id,
        ),
        limit=3,
    )

    assert [message.id for message in first] == ids
    assert [message.id for message in second] == [ids[2]]

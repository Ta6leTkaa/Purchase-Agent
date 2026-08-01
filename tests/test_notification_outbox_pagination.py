from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.services.notification_outbox_pagination import (
    InvalidNotificationOutboxCursorError,
    NotificationOutboxCursor,
    NotificationOutboxCursorCodec,
)


def test_notification_outbox_cursor_round_trip() -> None:
    cursor = NotificationOutboxCursor(
        occurred_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
        message_id=uuid4(),
    )

    encoded = NotificationOutboxCursorCodec().encode(cursor)

    assert NotificationOutboxCursorCodec().decode(encoded) == cursor


@pytest.mark.parametrize("value", ["invalid", "e30", "eyJ2Ijo5OTl9"])
def test_notification_outbox_cursor_rejects_invalid_value(value: str) -> None:
    with pytest.raises(InvalidNotificationOutboxCursorError):
        NotificationOutboxCursorCodec().decode(value)


def test_notification_outbox_cursor_requires_aware_time() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        NotificationOutboxCursor(
            occurred_at=datetime(2026, 8, 1, 10, 0),
            message_id=uuid4(),
        )

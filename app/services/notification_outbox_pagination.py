import base64
import binascii
import json
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

_CURSOR_VERSION = 1


class InvalidNotificationOutboxCursorError(ValueError):
    """Raised when an outbox cursor cannot be decoded safely."""


class NotificationOutboxCursor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    occurred_at: datetime
    message_id: UUID

    @model_validator(mode="after")
    def validate_occurred_at(self) -> "NotificationOutboxCursor":
        if (
            self.occurred_at.tzinfo is None
            or self.occurred_at.utcoffset() is None
        ):
            raise ValueError("cursor occurred_at must be timezone-aware")
        return self


class NotificationOutboxCursorCodec:
    def encode(self, cursor: NotificationOutboxCursor) -> str:
        payload = {
            "message_id": str(cursor.message_id),
            "occurred_at": cursor.occurred_at.isoformat(),
            "v": _CURSOR_VERSION,
        }
        encoded = json.dumps(
            payload,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return base64.urlsafe_b64encode(encoded).decode().rstrip("=")

    def decode(self, value: str) -> NotificationOutboxCursor:
        try:
            padding = "=" * (-len(value) % 4)
            payload = json.loads(
                base64.urlsafe_b64decode(f"{value}{padding}").decode()
            )
            if (
                not isinstance(payload, dict)
                or payload.pop("v", None) != _CURSOR_VERSION
            ):
                raise ValueError("unsupported cursor version")
            return NotificationOutboxCursor.model_validate(payload)
        except (
            binascii.Error,
            UnicodeDecodeError,
            ValueError,
            ValidationError,
            json.JSONDecodeError,
        ) as exc:
            raise InvalidNotificationOutboxCursorError(
                "Notification outbox cursor is invalid"
            ) from exc

import base64
import binascii
import json
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError

_CURSOR_VERSION = 1


class InvalidMissionCursorError(ValueError):
    """Raised when a mission listing cursor cannot be decoded safely."""


class MissionCursor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mission_id: UUID


class MissionCursorCodec:
    def encode(self, cursor: MissionCursor) -> str:
        payload = {
            "mission_id": str(cursor.mission_id),
            "v": _CURSOR_VERSION,
        }
        encoded = json.dumps(
            payload,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return base64.urlsafe_b64encode(encoded).decode().rstrip("=")

    def decode(self, value: str) -> MissionCursor:
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
            return MissionCursor.model_validate(payload)
        except (
            binascii.Error,
            UnicodeDecodeError,
            ValueError,
            ValidationError,
            json.JSONDecodeError,
        ) as exc:
            raise InvalidMissionCursorError(
                "Mission cursor is invalid"
            ) from exc

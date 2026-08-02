import base64
import binascii
import json
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError

_CURSOR_VERSION = 1


class InvalidIdentityCursorError(ValueError):
    """Raised when an identity listing cursor cannot be decoded safely."""


class IdentityCursor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    identity_id: UUID


class IdentityCursorCodec:
    def encode(self, cursor: IdentityCursor) -> str:
        payload = {
            "identity_id": str(cursor.identity_id),
            "v": _CURSOR_VERSION,
        }
        encoded = json.dumps(
            payload,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return base64.urlsafe_b64encode(encoded).decode().rstrip("=")

    def decode(self, value: str) -> IdentityCursor:
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
            return IdentityCursor.model_validate(payload)
        except (
            binascii.Error,
            UnicodeDecodeError,
            ValueError,
            ValidationError,
            json.JSONDecodeError,
        ) as exc:
            raise InvalidIdentityCursorError(
                "Identity cursor is invalid"
            ) from exc

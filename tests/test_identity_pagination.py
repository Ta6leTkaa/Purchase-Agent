from uuid import uuid4

import pytest

from app.services.identity_pagination import (
    IdentityCursor,
    IdentityCursorCodec,
    InvalidIdentityCursorError,
)


def test_identity_cursor_round_trip() -> None:
    cursor = IdentityCursor(identity_id=uuid4())

    encoded = IdentityCursorCodec().encode(cursor)

    assert IdentityCursorCodec().decode(encoded) == cursor


@pytest.mark.parametrize("value", ["invalid", "e30", ""])
def test_identity_cursor_rejects_invalid_value(value: str) -> None:
    with pytest.raises(InvalidIdentityCursorError):
        IdentityCursorCodec().decode(value)

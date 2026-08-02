from uuid import uuid4

import pytest

from app.services.mission_pagination import (
    InvalidMissionCursorError,
    MissionCursor,
    MissionCursorCodec,
)


def test_mission_cursor_round_trip() -> None:
    cursor = MissionCursor(mission_id=uuid4())

    encoded = MissionCursorCodec().encode(cursor)

    assert MissionCursorCodec().decode(encoded) == cursor


@pytest.mark.parametrize("value", ["invalid", "e30", ""])
def test_mission_cursor_rejects_invalid_value(value: str) -> None:
    with pytest.raises(InvalidMissionCursorError):
        MissionCursorCodec().decode(value)

from datetime import date

import pytest

from app.domain.mission import TrainTicketMissionPayload


def test_train_ticket_payload_normalizes_and_is_immutable() -> None:
    payload = TrainTicketMissionPayload(
        origin=" Amsterdam ",
        destination=" Berlin ",
        departure_date=date(2026, 9, 15),
    )

    assert payload.origin == "Amsterdam"
    assert payload.destination == "Berlin"
    with pytest.raises(Exception):
        setattr(payload, "origin", "Paris")


@pytest.mark.parametrize(
    ("origin", "destination"),
    [
        ("", "Berlin"),
        ("Amsterdam", " "),
        ("A" * 201, "Berlin"),
        ("Amsterdam", "B" * 201),
        ("Amsterdam", " amsterdam "),
    ],
)
def test_train_ticket_payload_rejects_invalid_cities(
    origin: str,
    destination: str,
) -> None:
    with pytest.raises(ValueError):
        TrainTicketMissionPayload(
            origin=origin,
            destination=destination,
            departure_date=date(2026, 9, 15),
        )

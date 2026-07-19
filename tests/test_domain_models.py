from datetime import date, datetime
from uuid import uuid4

from app.domain.execution import ExecutionEvent
from app.domain.identity import Document, DocumentType, Identity
from app.domain.mission import Mission, MissionStatus, MissionType, TrainConstraints
from app.domain.provider import ProviderOption, ProviderOptionType, Seat, SeatBerth


def test_create_identity_with_document() -> None:
    document = Document(
        id=uuid4(),
        type=DocumentType.internal_passport,
        number="1234567890",
    )

    identity = Identity(
        id=uuid4(),
        display_name="Ivan Petrov",
        first_name="Ivan",
        last_name="Petrov",
        birth_date=date(1990, 1, 1),
        documents=[document],
    )

    assert identity.documents == [document]
    assert identity.documents[0].type is DocumentType.internal_passport


def test_create_train_trip_mission_with_default_created_status() -> None:
    mission = Mission(
        id=uuid4(),
        type=MissionType.train_trip,
        title="Moscow to Saint Petersburg",
        participant_ids=[uuid4()],
        provider="rzd",
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Saint Petersburg",
            travel_date=date(2026, 8, 1),
            passengers_count=1,
        ),
    )

    assert mission.type is MissionType.train_trip
    assert mission.mission_type is MissionType.TRAIN_TICKET
    assert mission.status is MissionStatus.created


def test_add_execution_event_to_mission() -> None:
    mission = Mission(
        id=uuid4(),
        type=MissionType.train_trip,
        title="Moscow to Kazan",
        participant_ids=[uuid4()],
        provider="rzd",
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Kazan",
            travel_date=date(2026, 8, 2),
            passengers_count=1,
        ),
    )
    event = ExecutionEvent(
        timestamp=datetime(2026, 7, 9, 10, 0),
        type="mission.created",
        message="Mission created.",
    )

    mission.execution_log.append(event)

    assert mission.execution_log == [event]


def test_create_provider_option_with_four_seats() -> None:
    seats = [
        Seat(
            carriage_number=1,
            compartment_number=1,
            seat_number=seat_number,
            berth=SeatBerth.lower if seat_number % 2 else SeatBerth.upper,
        )
        for seat_number in range(1, 5)
    ]

    option = ProviderOption(
        id=uuid4(),
        type=ProviderOptionType.train_option,
        train_number="001A",
        from_city="Moscow",
        to_city="Saint Petersburg",
        departure_at=datetime(2026, 8, 1, 20, 0),
        arrival_at=datetime(2026, 8, 2, 6, 0),
        total_price=12000,
        seats=seats,
    )

    assert len(option.seats) == 4
    assert option.type is ProviderOptionType.train_option

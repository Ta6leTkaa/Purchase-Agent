import asyncio
from datetime import date
from uuid import uuid4

import pytest

from app.adapters import get_adapter
from app.adapters.mock_train import MockTrainAdapter
from app.domain.mission import Mission, MissionType, TrainConstraints
from app.domain.provider_capability import ProviderCapability


def make_mission() -> Mission:
    return Mission(
        id=uuid4(),
        type=MissionType.train_trip,
        title="Moscow to Saint Petersburg",
        participant_ids=[uuid4()],
        provider="mock_train",
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Saint Petersburg",
            travel_date=date(2026, 8, 1),
            passengers_count=4,
        ),
    )


def test_get_adapter_returns_mock_train_adapter() -> None:
    adapter = get_adapter("mock_train")

    assert isinstance(adapter, MockTrainAdapter)


def test_mock_train_adapter_declares_train_ticket_capability() -> None:
    adapter = MockTrainAdapter()

    assert adapter.provider_id == "mock_train"
    assert adapter.capabilities == frozenset(
        {
            ProviderCapability(
                mission_type=MissionType.TRAIN_TICKET,
            )
        }
    )


def test_search_options_returns_four_options() -> None:
    adapter = MockTrainAdapter()

    options = asyncio.run(adapter.search_options(make_mission(), []))

    assert len(options) == 4


def test_variant_a_has_four_seats_in_one_compartment() -> None:
    adapter = MockTrainAdapter()

    options = asyncio.run(adapter.search_options(make_mission(), []))
    variant_a = options[0]
    compartments = {seat.compartment_number for seat in variant_a.seats}

    assert variant_a.train_number == "001A"
    assert len(variant_a.seats) == 4
    assert compartments == {1}


def test_variant_c_has_seats_in_two_adjacent_compartments() -> None:
    adapter = MockTrainAdapter()

    options = asyncio.run(adapter.search_options(make_mission(), []))
    variant_c = options[2]
    compartments = sorted({seat.compartment_number for seat in variant_c.seats})

    assert variant_c.train_number == "003C"
    assert compartments == [3, 4]


def test_reserve_option_returns_success_with_confirmation_required() -> None:
    adapter = MockTrainAdapter()
    mission = make_mission()
    option = asyncio.run(adapter.search_options(mission, []))[0]

    result = asyncio.run(adapter.reserve_option(option, mission))

    assert result.success is True
    assert result.requires_confirmation is True
    assert result.reservation_id == f"mock-reservation-{option.id}"


def test_unknown_provider_raises_value_error() -> None:
    with pytest.raises(ValueError):
        get_adapter("unknown")

import json
from datetime import UTC, date, datetime
from uuid import uuid4

import httpx
import pytest

from app.adapters.http_train import HttpTrainAdapter
from app.domain.identity import Document, DocumentType, Identity
from app.domain.mission import Mission, TrainConstraints
from app.domain.provider import ProviderOption, ProviderOptionType, Seat, SeatBerth
from app.services.provider_errors import ProviderOperationError


def make_identity() -> Identity:
    return Identity(
        id=uuid4(),
        display_name="Passenger",
        first_name="Ivan",
        last_name="Ivanov",
        birth_date=date(1990, 1, 1),
        documents=[
            Document(
                id=uuid4(),
                type=DocumentType.internal_passport,
                number="1234567890",
            )
        ],
    )


def make_mission() -> Mission:
    return Mission(
        id=uuid4(),
        title="Moscow to Saint Petersburg",
        participant_ids=[uuid4()],
        provider="http_train",
        provider_id="http_train",
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Saint Petersburg",
            travel_date=date(2026, 9, 1),
            passengers_count=1,
        ),
    )


def option_payload() -> dict[str, object]:
    return {
        "id": str(uuid4()),
        "type": "train_option",
        "train_number": "752A",
        "from_city": "Moscow",
        "to_city": "Saint Petersburg",
        "departure_at": "2026-09-01T08:00:00Z",
        "arrival_at": "2026-09-01T12:00:00Z",
        "total_price": 6500,
        "seats": [
            {
                "carriage_number": 5,
                "compartment_number": 2,
                "seat_number": 8,
                "berth": "upper",
                "near_toilet": False,
            }
        ],
        "metadata": {"fare": "standard"},
    }


def make_option() -> ProviderOption:
    return ProviderOption(
        id=uuid4(),
        type=ProviderOptionType.train_option,
        train_number="752A",
        from_city="Moscow",
        to_city="Saint Petersburg",
        departure_at=datetime(2026, 9, 1, 8, tzinfo=UTC),
        arrival_at=datetime(2026, 9, 1, 12, tzinfo=UTC),
        total_price=6500,
        seats=[
            Seat(
                carriage_number=5,
                compartment_number=2,
                seat_number=8,
                berth=SeatBerth.upper,
            )
        ],
    )


@pytest.mark.asyncio
async def test_search_sends_typed_passenger_request_and_parses_options() -> None:
    captured: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = request
        return httpx.Response(200, json={"options": [option_payload()]})

    adapter = HttpTrainAdapter(
        base_url="https://trains.example.test/",
        bearer_token="provider-secret",
        transport=httpx.MockTransport(handler),
    )
    mission = make_mission()
    identity = make_identity()

    options = await adapter.search_options(mission, [identity])

    assert len(options) == 1
    assert options[0].train_number == "752A"
    assert captured is not None
    assert captured.url == "https://trains.example.test/v1/train/options/search"
    assert captured.headers["authorization"] == "Bearer provider-secret"
    request_payload = json.loads(captured.content)
    assert request_payload["mission_id"] == str(mission.id)
    assert request_payload["passengers"][0]["documents"][0]["number"] == (
        "1234567890"
    )


@pytest.mark.asyncio
async def test_reservation_lifecycle_forwards_idempotency_keys() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/confirm"):
            return httpx.Response(200, json={"success": True, "message": "ok"})
        if request.url.path.endswith("/cancel"):
            return httpx.Response(200, json={"success": True, "message": "ok"})
        return httpx.Response(
            200,
            json={
                "success": True,
                "reservation_id": "real-reservation-42",
                "requires_confirmation": True,
                "message": "reserved",
            },
        )

    adapter = HttpTrainAdapter(
        base_url="https://trains.example.test",
        transport=httpx.MockTransport(handler),
    )
    mission = make_mission()
    reservation = await adapter.reserve_option(
        make_option(),
        mission,
        idempotency_key="reserve-key",
    )
    confirmation = await adapter.confirm_reservation(
        "real-reservation-42",
        mission,
        idempotency_key="confirm-key",
    )
    cancellation = await adapter.cancel_reservation(
        "real-reservation-42",
        mission,
        idempotency_key="cancel-key",
    )

    assert reservation.reservation_id == "real-reservation-42"
    assert confirmation.success
    assert cancellation.success
    assert [request.headers["idempotency-key"] for request in requests] == [
        "reserve-key",
        "confirm-key",
        "cancel-key",
    ]
    assert [request.url.path for request in requests] == [
        "/v1/train/reservations",
        "/v1/train/reservations/real-reservation-42/confirm",
        "/v1/train/reservations/real-reservation-42/cancel",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "retryable"),
    [(400, False), (409, False), (429, True), (503, True)],
)
async def test_http_errors_are_sanitized_and_classified(
    status_code: int,
    retryable: bool,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(status_code, text="upstream sensitive failure")

    adapter = HttpTrainAdapter(
        base_url="https://trains.example.test",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ProviderOperationError) as exc_info:
        await adapter.search_options(make_mission(), [make_identity()])

    assert exc_info.value.provider_id == "http_train"
    assert exc_info.value.operation == "search"
    assert exc_info.value.retryable is retryable
    assert "sensitive" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_invalid_upstream_payload_is_retryable_provider_failure() -> None:
    adapter = HttpTrainAdapter(
        base_url="https://trains.example.test",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"options": [{"bad": True}]})
        ),
    )

    with pytest.raises(ProviderOperationError) as exc_info:
        await adapter.search_options(make_mission(), [])

    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_network_failure_is_retryable_provider_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("provider unavailable", request=request)

    adapter = HttpTrainAdapter(
        base_url="https://trains.example.test",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ProviderOperationError) as exc_info:
        await adapter.search_options(make_mission(), [])

    assert exc_info.value.retryable is True

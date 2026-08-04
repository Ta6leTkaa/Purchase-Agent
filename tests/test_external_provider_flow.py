import asyncio
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from app.adapters.http_train import HttpTrainAdapter
from app.adapters.registry import ProviderRegistry
from app.dependencies import (
    get_provider_registry,
    get_provider_resolver,
    identity_repository,
    mission_command_idempotency_store,
    mission_repository,
    resource_creation_idempotency_store,
)
from app.main import app
from app.services.provider_resolver import ProviderResolver


def test_external_provider_purchase_flow_is_actionable_and_idempotent() -> None:
    provider_requests: list[httpx.Request] = []

    async def gateway(request: httpx.Request) -> httpx.Response:
        provider_requests.append(request)
        if request.url.path == "/v1/train/options/search":
            return httpx.Response(200, json={"options": [_option_payload()]})
        if request.url.path == "/v1/train/reservations":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "reservation_id": "gateway-reservation-1",
                    "requires_confirmation": True,
                    "message": "Reservation held.",
                },
            )
        if request.url.path.endswith("/confirm"):
            return httpx.Response(
                200,
                json={"success": True, "message": "Purchase confirmed."},
            )
        raise AssertionError(f"Unexpected provider request: {request.url}")

    adapter = HttpTrainAdapter(
        base_url="https://gateway.example.test",
        bearer_token="gateway-secret",
        transport=httpx.MockTransport(gateway),
    )
    registry = ProviderRegistry([adapter])
    app.dependency_overrides[get_provider_registry] = lambda: registry
    app.dependency_overrides[get_provider_resolver] = lambda: ProviderResolver(
        registry
    )
    _clear_state()
    try:
        with TestClient(app) as client:
            _assert_purchase_flow(client, provider_requests)
    finally:
        app.dependency_overrides.clear()
        _clear_state()


def _assert_purchase_flow(
    client: TestClient,
    provider_requests: list[httpx.Request],
) -> None:
    identity_response = client.post(
        "/identities",
        headers={"Idempotency-Key": "e2e-create-passenger"},
        json={
            "display_name": "Ivan Ivanov",
            "first_name": "Ivan",
            "last_name": "Ivanov",
            "birth_date": "1990-01-01",
            "documents": [
                {"type": "internal_passport", "number": "1234567890"}
            ],
        },
    )
    assert identity_response.status_code == 200
    create_response = client.post(
        "/missions",
        headers={"Idempotency-Key": "e2e-create-mission"},
        json=_mission_payload(identity_response.json()["id"]),
    )
    assert create_response.status_code == 200
    mission_id = create_response.json()["id"]
    initial_outcome = client.get(f"/missions/{mission_id}/outcome")
    assert initial_outcome.json()["next_action"] == "run"
    assert initial_outcome.headers["etag"] == create_response.headers["etag"]
    assert initial_outcome.headers["cache-control"] == "private, no-cache"

    run_response = client.post(
        f"/missions/{mission_id}/run",
        headers={
            "Idempotency-Key": "e2e-run-mission",
            "If-Match": create_response.headers["etag"],
        },
    )
    run_replay = client.post(
        f"/missions/{mission_id}/run",
        headers={"Idempotency-Key": "e2e-run-mission"},
    )
    assert run_response.status_code == 200
    assert run_replay.json() == run_response.json()
    assert run_response.json()["status"] == "requires_confirmation"
    assert run_response.json()["resolved_provider_id"] == "http_train"
    assert run_response.json()["reservation_id"] == "gateway-reservation-1"
    pending = client.get(f"/missions/{mission_id}/outcome").json()
    assert pending["next_action"] == "confirm"
    assert pending["selected_option"]["train_number"] == "752A"

    confirm_response = client.post(
        f"/missions/{mission_id}/confirm",
        headers={
            "Idempotency-Key": "e2e-confirm-mission",
            "If-Match": run_response.headers["etag"],
        },
    )
    confirm_replay = client.post(
        f"/missions/{mission_id}/confirm",
        headers={"Idempotency-Key": "e2e-confirm-mission"},
    )
    assert confirm_response.status_code == 200
    assert confirm_replay.json() == confirm_response.json()
    final = client.get(f"/missions/{mission_id}/outcome").json()
    assert final["status"] == "completed"
    assert final["terminal"] is True
    assert final["successful"] is True
    assert final["next_action"] == "none"
    assert final["reservation_id"] == "gateway-reservation-1"
    assert [request.url.path for request in provider_requests] == [
        "/v1/train/options/search",
        "/v1/train/reservations",
        "/v1/train/reservations/gateway-reservation-1/confirm",
    ]
    assert all(
        request.headers["authorization"] == "Bearer gateway-secret"
        for request in provider_requests
    )


def _mission_payload(identity_id: str) -> dict[str, object]:
    return {
        "type": "train_trip",
        "title": "Moscow to Saint Petersburg",
        "participant_ids": [identity_id],
        "provider": "http_train",
        "provider_id": "http_train",
        "execution_mode": "require_confirmation",
        "payload": {
            "origin": "Moscow",
            "destination": "Saint Petersburg",
            "departure_date": "2026-09-01",
        },
        "constraints": {
            "from_city": "Moscow",
            "to_city": "Saint Petersburg",
            "travel_date": "2026-09-01",
            "passengers_count": 1,
            "max_total_price": 10000,
        },
    }


def _option_payload() -> dict[str, object]:
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
                "berth": "lower",
                "near_toilet": False,
            }
        ],
        "metadata": {"fare": "standard"},
    }


def _clear_state() -> None:
    asyncio.run(identity_repository.clear())
    asyncio.run(mission_repository.clear())
    asyncio.run(mission_command_idempotency_store.clear())
    asyncio.run(resource_creation_idempotency_store.clear())

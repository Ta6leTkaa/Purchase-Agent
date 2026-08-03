import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.dependencies import (
    identity_repository,
    mission_command_idempotency_store,
    mission_repository,
    resource_creation_idempotency_store,
)
from app.main import app


@pytest.fixture(autouse=True)
def clear_repositories() -> Iterator[None]:
    asyncio.run(identity_repository.clear())
    asyncio.run(mission_repository.clear())
    asyncio.run(mission_command_idempotency_store.clear())
    asyncio.run(resource_creation_idempotency_store.clear())
    yield
    asyncio.run(identity_repository.clear())
    asyncio.run(mission_repository.clear())
    asyncio.run(mission_command_idempotency_store.clear())
    asyncio.run(resource_creation_idempotency_store.clear())


def make_mission_payload(
    *,
    participant_ids: list[str] | None = None,
    passengers_count: int = 1,
    provider: str = "rzd",
) -> dict[str, object]:
    if participant_ids is None:
        participant_ids = [str(uuid4())]

    return {
        "type": "train_trip",
        "title": "Moscow to Saint Petersburg",
        "participant_ids": participant_ids,
        "provider": provider,
        "payload": {
            "origin": "Moscow",
            "destination": "Saint Petersburg",
            "departure_date": "2026-08-01",
        },
        "constraints": {
            "from_city": "Moscow",
            "to_city": "Saint Petersburg",
            "travel_date": "2026-08-01",
            "passengers_count": passengers_count,
        },
    }


def create_identity(client: TestClient) -> str:
    response = client.post("/identities", json=make_identity_payload())
    return str(response.json()["id"])


def make_existing_participant_ids(
    client: TestClient,
    count: int = 1,
) -> list[str]:
    return [create_identity(client) for _ in range(count)]


def test_post_missions_creates_mission() -> None:
    client = TestClient(app)
    payload = make_mission_payload(
        participant_ids=make_existing_participant_ids(client)
    )

    response = client.post("/missions", json=payload)

    assert response.status_code == 200
    assert response.json()["id"] is not None
    assert response.json()["status"] == "created"


def test_post_missions_replays_same_idempotent_creation() -> None:
    client = TestClient(app)
    payload = make_mission_payload(
        participant_ids=make_existing_participant_ids(client)
    )
    headers = {"Idempotency-Key": "create-mission-once"}

    first = client.post("/missions", json=payload, headers=headers)
    replay = client.post("/missions", json=payload, headers=headers)

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert replay.headers["etag"] == first.headers["etag"]
    assert len(client.get("/missions").json()) == 1


def test_post_missions_rejects_idempotency_key_payload_conflict() -> None:
    client = TestClient(app)
    payload = make_mission_payload(
        participant_ids=make_existing_participant_ids(client)
    )
    headers = {"Idempotency-Key": "conflicting-mission-create"}
    client.post("/missions", json=payload, headers=headers)

    response = client.post(
        "/missions",
        json={**payload, "title": "Another trip"},
        headers=headers,
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "idempotency_key_conflict"


def test_post_missions_without_id_generates_uuid() -> None:
    client = TestClient(app)
    payload = make_mission_payload(
        participant_ids=make_existing_participant_ids(client)
    )

    response = client.post("/missions", json=payload)

    assert response.status_code == 200
    assert UUID(response.json()["id"])


def test_post_missions_initializes_internal_fields() -> None:
    client = TestClient(app)
    payload = make_mission_payload(
        participant_ids=make_existing_participant_ids(client)
    )

    response = client.post("/missions", json=payload)

    assert response.status_code == 200
    assert response.json()["status"] == "created"
    assert response.json()["execution_log"] == []
    assert response.json()["best_option"] is None
    assert response.json()["execution_attempts"] == 0
    assert response.json()["max_execution_attempts"] == 3
    assert response.json()["execution_mode"] == "require_confirmation"
    assert response.json()["mission_type"] == "train_ticket"
    assert response.json()["payload"] == payload["payload"]
    assert response.json()["provider_id"] is None
    assert response.json()["resolved_provider_id"] is None
    assert response.json()["reservation_id"] is None


@pytest.mark.parametrize(
    "execution_mode",
    ["search_only", "require_confirmation", "auto_purchase"],
)
def test_post_missions_accepts_execution_mode(
    execution_mode: str,
) -> None:
    client = TestClient(app)
    payload = {
        **make_mission_payload(
            participant_ids=make_existing_participant_ids(client)
        ),
        "execution_mode": execution_mode,
    }

    response = client.post("/missions", json=payload)

    assert response.status_code == 200
    assert response.json()["execution_mode"] == execution_mode


def test_post_missions_rejects_unknown_execution_mode() -> None:
    client = TestClient(app)
    payload = {
        **make_mission_payload(
            participant_ids=make_existing_participant_ids(client)
        ),
        "execution_mode": "buy_without_limits",
    }

    response = client.post("/missions", json=payload)

    assert response.status_code == 422


def test_post_missions_accepts_expiry_deadline() -> None:
    client = TestClient(app)
    payload = {
        **make_mission_payload(
            participant_ids=make_existing_participant_ids(client)
        ),
        "expires_at": "2030-08-01T12:00:00Z",
    }

    response = client.post("/missions", json=payload)

    assert response.status_code == 200
    assert response.json()["expires_at"] == "2030-08-01T12:00:00Z"


def test_post_missions_rejects_expiry_before_schedule() -> None:
    client = TestClient(app)
    payload = {
        **make_mission_payload(
            participant_ids=make_existing_participant_ids(client)
        ),
        "scheduled_at": "2030-08-01T12:00:00Z",
        "expires_at": "2030-08-01T11:00:00Z",
    }

    response = client.post("/missions", json=payload)

    assert response.status_code == 422


def test_patch_mission_updates_safe_configuration_with_etag() -> None:
    client = TestClient(app)
    created = client.post(
        "/missions",
        json=make_mission_payload(
            participant_ids=make_existing_participant_ids(client)
        ),
    )
    mission_id = created.json()["id"]

    response = client.patch(
        f"/missions/{mission_id}",
        headers={"If-Match": created.headers["etag"]},
        json={
            "title": "  Updated journey  ",
            "fallback_rules": {"allow_any_coupe_seats": True},
            "execution_mode": "search_only",
            "max_execution_attempts": 5,
        },
    )

    assert response.status_code == 200
    assert response.headers["etag"] == '"1"'
    assert response.json()["title"] == "Updated journey"
    assert response.json()["execution_mode"] == "search_only"
    assert response.json()["max_execution_attempts"] == 5
    assert response.json()["fallback_rules"]["allow_any_coupe_seats"] is True
    assert response.json()["execution_log"][-1]["type"] == "mission_updated"


def test_patch_mission_rejects_stale_version() -> None:
    client = TestClient(app)
    created = client.post(
        "/missions",
        json=make_mission_payload(
            participant_ids=make_existing_participant_ids(client)
        ),
    )
    mission_id = created.json()["id"]
    first = client.patch(
        f"/missions/{mission_id}",
        headers={"If-Match": '"0"'},
        json={"title": "First update"},
    )

    response = client.patch(
        f"/missions/{mission_id}",
        headers={"If-Match": '"0"'},
        json={"title": "Stale update"},
    )

    assert first.status_code == 200
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "mission_version_conflict"


@pytest.mark.parametrize(
    "payload",
    [{}, {"title": None}, {"title": "   "}, {"status": "cancelled"}],
)
def test_patch_mission_rejects_invalid_payload(
    payload: dict[str, object],
) -> None:
    client = TestClient(app)
    created = client.post(
        "/missions",
        json=make_mission_payload(
            participant_ids=make_existing_participant_ids(client)
        ),
    )

    response = client.patch(
        f"/missions/{created.json()['id']}",
        json=payload,
    )

    assert response.status_code == 422


def test_pause_and_resume_scheduled_mission() -> None:
    client = TestClient(app)
    payload = {
        **make_mission_payload(
            participant_ids=make_existing_participant_ids(client)
        ),
        "scheduled_at": "2030-08-01T10:00:00Z",
    }
    created = client.post("/missions", json=payload)
    mission_id = created.json()["id"]

    paused = client.post(
        f"/missions/{mission_id}/pause",
        headers={
            "Idempotency-Key": "pause-scheduled-mission",
            "If-Match": created.headers["etag"],
        },
    )
    resumed = client.post(
        f"/missions/{mission_id}/resume",
        headers={
            "Idempotency-Key": "resume-scheduled-mission",
            "If-Match": paused.headers["etag"],
        },
    )

    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"
    assert paused.json()["execution_log"][-1]["type"] == "mission_paused"
    assert paused.headers["etag"] == '"1"'
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "waiting"
    assert resumed.json()["execution_log"][-1]["type"] == "mission_resumed"
    assert resumed.headers["etag"] == '"2"'


def test_pause_command_is_idempotent() -> None:
    client = TestClient(app)
    created = client.post(
        "/missions",
        json=make_mission_payload(
            participant_ids=make_existing_participant_ids(client)
        ),
    )
    url = f"/missions/{created.json()['id']}/pause"
    headers = {"Idempotency-Key": "same-pause-command"}

    first = client.post(url, headers=headers)
    replay = client.post(url, headers=headers)

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert len(replay.json()["execution_log"]) == 1


def test_paused_mission_cannot_run_and_is_not_due() -> None:
    client = TestClient(app)
    created = client.post(
        "/missions",
        json={
            **make_mission_payload(
                participant_ids=make_existing_participant_ids(client)
            ),
            "scheduled_at": "2030-07-29T00:00:00Z",
        },
    )
    mission_id = created.json()["id"]
    paused = client.post(
        f"/missions/{mission_id}/pause",
        headers={"Idempotency-Key": "pause-due-mission"},
    )

    run = client.post(
        f"/missions/{mission_id}/run",
        headers={"Idempotency-Key": "run-paused-mission"},
    )
    due = asyncio.run(
        mission_repository.list_due(
            datetime(2030, 7, 30, tzinfo=UTC),
        )
    )

    assert paused.status_code == 200
    assert run.status_code == 409
    assert UUID(mission_id) not in {mission.id for mission in due}


def test_pause_and_resume_reject_invalid_states() -> None:
    client = TestClient(app)
    created = client.post(
        "/missions",
        json=make_mission_payload(
            participant_ids=make_existing_participant_ids(client)
        ),
    )
    mission_id = created.json()["id"]

    resume = client.post(
        f"/missions/{mission_id}/resume",
        headers={"Idempotency-Key": "resume-created"},
    )
    client.post(
        f"/missions/{mission_id}/run",
        headers={"Idempotency-Key": "run-created"},
    )
    pause = client.post(
        f"/missions/{mission_id}/pause",
        headers={"Idempotency-Key": "pause-finished"},
    )

    assert resume.status_code == 409
    assert resume.json()["detail"]["code"] == "mission_resume_not_allowed"
    assert pause.status_code == 409
    assert pause.json()["detail"]["code"] == "mission_pause_not_allowed"


def test_post_missions_rejects_client_supplied_reservation_id() -> None:
    client = TestClient(app)
    payload = {
        **make_mission_payload(
            participant_ids=make_existing_participant_ids(client)
        ),
        "reservation_id": "provider-reservation-123",
    }

    response = client.post("/missions", json=payload)

    assert response.status_code == 422


def test_post_missions_accepts_and_normalizes_provider_id() -> None:
    client = TestClient(app)
    payload = {
        **make_mission_payload(
            participant_ids=make_existing_participant_ids(client)
        ),
        "provider_id": "  mock_train  ",
    }

    response = client.post("/missions", json=payload)

    assert response.status_code == 200
    assert response.json()["provider_id"] == "mock_train"
    stored_mission = asyncio.run(
        mission_repository.get(UUID(response.json()["id"]))
    )
    assert stored_mission is not None
    assert stored_mission.provider_id == "mock_train"


def test_post_missions_accepts_unregistered_provider_id() -> None:
    client = TestClient(app)
    payload = {
        **make_mission_payload(
            participant_ids=make_existing_participant_ids(client)
        ),
        "provider_id": "not_registered_yet",
    }

    response = client.post("/missions", json=payload)

    assert response.status_code == 200
    assert response.json()["provider_id"] == "not_registered_yet"


@pytest.mark.parametrize("provider_id", ["", "   "])
def test_post_missions_rejects_empty_provider_id(provider_id: str) -> None:
    client = TestClient(app)
    payload = {
        **make_mission_payload(
            participant_ids=make_existing_participant_ids(client)
        ),
        "provider_id": provider_id,
    }

    response = client.post("/missions", json=payload)

    assert response.status_code == 422


def test_post_missions_accepts_train_ticket_mission_type() -> None:
    client = TestClient(app)
    payload = {
        **make_mission_payload(
            participant_ids=make_existing_participant_ids(client)
        ),
        "mission_type": "train_ticket",
    }

    response = client.post("/missions", json=payload)

    assert response.status_code == 200
    assert response.json()["mission_type"] == "train_ticket"


def test_post_missions_rejects_unknown_mission_type() -> None:
    client = TestClient(app)
    payload = {
        **make_mission_payload(
            participant_ids=make_existing_participant_ids(client)
        ),
        "mission_type": "flight_ticket",
    }

    response = client.post("/missions", json=payload)

    assert response.status_code == 422


def test_post_missions_accepts_custom_max_execution_attempts() -> None:
    client = TestClient(app)
    payload = {
        **make_mission_payload(
            participant_ids=make_existing_participant_ids(client)
        ),
        "max_execution_attempts": 5,
    }

    response = client.post("/missions", json=payload)

    assert response.status_code == 200
    assert response.json()["max_execution_attempts"] == 5


def test_post_missions_with_scheduled_at_returns_waiting() -> None:
    client = TestClient(app)
    scheduled_at = datetime.now(UTC) + timedelta(days=1)
    payload = {
        **make_mission_payload(
            participant_ids=make_existing_participant_ids(client)
        ),
        "scheduled_at": scheduled_at.isoformat(),
    }

    response = client.post("/missions", json=payload)

    assert response.status_code == 200
    assert response.json()["status"] == "waiting"
    assert response.json()["scheduled_at"] is not None


def test_post_missions_with_naive_scheduled_at_returns_422() -> None:
    client = TestClient(app)
    payload = {
        **make_mission_payload(
            participant_ids=make_existing_participant_ids(client)
        ),
        "scheduled_at": "2026-08-01T12:00:00",
    }

    response = client.post("/missions", json=payload)

    assert response.status_code == 422


def test_post_missions_with_past_scheduled_at_returns_422() -> None:
    client = TestClient(app)
    scheduled_at = datetime.now(UTC) - timedelta(days=1)
    payload = {
        **make_mission_payload(
            participant_ids=make_existing_participant_ids(client)
        ),
        "scheduled_at": scheduled_at.isoformat(),
    }

    response = client.post("/missions", json=payload)

    assert response.status_code == 422


def test_get_missions_returns_created_mission() -> None:
    client = TestClient(app)
    payload = make_mission_payload(
        participant_ids=make_existing_participant_ids(client)
    )
    create_response = client.post("/missions", json=payload)

    response = client.get("/missions")

    assert response.status_code == 200
    assert response.json()[0]["id"] == create_response.json()["id"]


def test_get_missions_filters_by_status() -> None:
    client = TestClient(app)
    first = client.post(
        "/missions",
        json=make_mission_payload(
            participant_ids=make_existing_participant_ids(client)
        ),
    )
    second = client.post(
        "/missions",
        json=make_mission_payload(
            participant_ids=make_existing_participant_ids(client)
        ),
    )
    client.post(
        f"/missions/{second.json()['id']}/cancel",
        headers={"Idempotency-Key": "cancel-for-list-filter"},
    )

    response = client.get("/missions", params={"status": "cancelled"})

    assert response.status_code == 200
    assert [mission["id"] for mission in response.json()] == [
        second.json()["id"]
    ]
    assert first.json()["id"] not in {
        mission["id"] for mission in response.json()
    }


def test_get_missions_filters_by_type_and_applies_limit() -> None:
    client = TestClient(app)
    for _ in range(2):
        client.post(
            "/missions",
            json=make_mission_payload(
                participant_ids=make_existing_participant_ids(client)
            ),
        )

    response = client.get(
        "/missions",
        params={"type": "train_ticket", "limit": 1},
    )

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_get_mission_summaries_omits_heavy_and_sensitive_fields() -> None:
    client = TestClient(app)
    created = client.post(
        "/missions",
        json=make_mission_payload(
            participant_ids=make_existing_participant_ids(client)
        ),
    )
    client.post(
        f"/missions/{created.json()['id']}/run",
        headers={"Idempotency-Key": "run-for-summary"},
    )

    response = client.get(
        "/missions/summaries",
        params={"status": "requires_confirmation", "limit": 1},
    )

    assert response.status_code == 200
    assert response.json()[0]["id"] == created.json()["id"]
    assert response.json()[0]["participant_count"] == len(
        created.json()["participant_ids"]
    )
    assert response.json()[0]["last_event_sequence"] > 0
    assert "participant_ids" not in response.json()[0]
    assert "constraints" not in response.json()[0]
    assert "execution_log" not in response.json()[0]
    assert "best_option" not in response.json()[0]


def test_get_mission_summary_pages_preserve_filters() -> None:
    client = TestClient(app)
    participant_ids = make_existing_participant_ids(client)
    created_ids = {
        client.post(
            "/missions",
            json=make_mission_payload(participant_ids=participant_ids),
        ).json()["id"]
        for _ in range(3)
    }

    first = client.get(
        "/missions/summaries/page",
        params={"type": "train_ticket", "status": "created", "limit": 2},
    )

    assert first.status_code == 200
    assert first.json()["has_more"] is True
    second = client.get(
        "/missions/summaries/page",
        params={
            "type": "train_ticket",
            "status": "created",
            "limit": 2,
            "cursor": first.json()["next_cursor"],
        },
    )
    assert second.status_code == 200
    assert second.json()["has_more"] is False
    assert second.json()["next_cursor"] is None
    returned_ids = {
        item["id"] for item in first.json()["items"] + second.json()["items"]
    }
    assert returned_ids == created_ids


def test_get_mission_summary_page_rejects_invalid_cursor() -> None:
    response = TestClient(app).get(
        "/missions/summaries/page",
        params={"cursor": "invalid"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_mission_cursor"


@pytest.mark.parametrize(
    "params",
    [
        {"status": "unknown"},
        {"type": "unknown"},
        {"limit": 0},
        {"limit": 501},
    ],
)
def test_get_missions_rejects_invalid_filters(
    params: dict[str, object],
) -> None:
    response = TestClient(app).get("/missions", params=params)

    assert response.status_code == 422


def test_get_mission_by_id_returns_created_mission() -> None:
    client = TestClient(app)
    payload = make_mission_payload(
        participant_ids=make_existing_participant_ids(client)
    )
    create_response = client.post("/missions", json=payload)
    mission_id = create_response.json()["id"]

    response = client.get(f"/missions/{mission_id}")

    assert response.status_code == 200
    assert response.json()["id"] == mission_id


def test_get_unknown_mission_returns_404() -> None:
    client = TestClient(app)

    response = client.get(f"/missions/{uuid4()}")

    assert response.status_code == 404


def test_post_missions_with_empty_participant_ids_returns_422() -> None:
    client = TestClient(app)
    payload = make_mission_payload(participant_ids=[])

    response = client.post("/missions", json=payload)

    assert response.status_code == 422


def test_post_missions_with_zero_passengers_count_returns_422() -> None:
    client = TestClient(app)
    payload = make_mission_payload(
        participant_ids=make_existing_participant_ids(client),
        passengers_count=0,
    )

    response = client.post("/missions", json=payload)

    assert response.status_code == 422


def test_post_missions_with_existing_participants_creates_mission() -> None:
    client = TestClient(app)
    participant_ids = make_existing_participant_ids(client, count=2)
    payload = make_mission_payload(
        participant_ids=participant_ids,
        passengers_count=2,
    )

    response = client.post("/missions", json=payload)

    assert response.status_code == 200
    assert response.json()["participant_ids"] == participant_ids


def test_post_missions_with_unknown_participant_returns_422() -> None:
    client = TestClient(app)
    unknown_participant_id = str(uuid4())
    payload = make_mission_payload(participant_ids=[unknown_participant_id])

    response = client.post("/missions", json=payload)

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "unknown_participants",
        "message": "One or more participants do not exist",
        "participant_ids": [unknown_participant_id],
    }
    assert asyncio.run(mission_repository.list()) == []


def test_post_missions_with_multiple_unknown_participants_returns_all() -> None:
    client = TestClient(app)
    participant_ids = [str(uuid4()), str(uuid4())]
    payload = make_mission_payload(
        participant_ids=participant_ids,
        passengers_count=2,
    )

    response = client.post("/missions", json=payload)

    assert response.status_code == 422
    assert response.json()["detail"]["participant_ids"] == participant_ids
    assert asyncio.run(mission_repository.list()) == []


def test_post_missions_with_duplicate_participant_ids_returns_422() -> None:
    client = TestClient(app)
    participant_id = create_identity(client)
    payload = make_mission_payload(
        participant_ids=[participant_id, participant_id],
        passengers_count=2,
    )

    response = client.post("/missions", json=payload)

    assert response.status_code == 422
    assert asyncio.run(mission_repository.list()) == []


def test_post_missions_with_passenger_count_mismatch_returns_422() -> None:
    client = TestClient(app)
    participant_ids = make_existing_participant_ids(client, count=2)
    payload = make_mission_payload(
        participant_ids=participant_ids,
        passengers_count=1,
    )

    response = client.post("/missions", json=payload)

    assert response.status_code == 422
    assert asyncio.run(mission_repository.list()) == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", str(uuid4())),
        ("status", "completed"),
        ("execution_log", []),
        ("best_option", None),
        ("claimed_at", datetime.now(UTC).isoformat()),
        ("execution_attempts", 1),
        ("resolved_provider_id", "mock_train"),
        ("reservation_id", "provider-reservation-123"),
    ],
)
def test_post_missions_with_internal_fields_returns_422(
    field: str,
    value: object,
) -> None:
    client = TestClient(app)
    payload = {
        **make_mission_payload(),
        field: value,
    }

    response = client.post("/missions", json=payload)

    assert response.status_code == 422


@pytest.mark.parametrize("max_execution_attempts", [0, 101])
def test_post_missions_rejects_invalid_max_execution_attempts(
    max_execution_attempts: int,
) -> None:
    client = TestClient(app)
    payload = {
        **make_mission_payload(
            participant_ids=make_existing_participant_ids(client)
        ),
        "max_execution_attempts": max_execution_attempts,
    }

    response = client.post("/missions", json=payload)

    assert response.status_code == 422


def make_identity_payload() -> dict[str, object]:
    return {
        "display_name": "Ivan Petrov",
        "first_name": "Ivan",
        "last_name": "Petrov",
        "birth_date": "1990-01-01",
        "documents": [],
    }


def test_post_mission_run_returns_requires_confirmation() -> None:
    client = TestClient(app)
    identity_payloads = [make_identity_payload() for _ in range(4)]
    identity_ids: list[str] = []
    for identity_payload in identity_payloads:
        response = client.post("/identities", json=identity_payload)
        identity_ids.append(response.json()["id"])
    mission_payload = make_mission_payload(
        participant_ids=identity_ids,
        passengers_count=4,
        provider="mock_train",
    )
    mission_payload["constraints"] = {
        "from_city": "Moscow",
        "to_city": "Saint Petersburg",
        "travel_date": "2026-08-01",
        "passengers_count": 4,
        "must_be_same_compartment": True,
        "min_lower_berths": 2,
        "max_total_price": 30000,
        "avoid_toilet": True,
    }
    create_response = client.post("/missions", json=mission_payload)
    mission_id = create_response.json()["id"]

    response = client.post(
        f"/missions/{mission_id}/run",
        headers={"Idempotency-Key": "run-success-key"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "requires_confirmation"


@pytest.mark.parametrize(
    ("execution_mode", "expected_status", "has_reservation"),
    [
        ("search_only", "completed", False),
        ("auto_purchase", "completed", True),
    ],
)
def test_post_mission_run_honors_execution_mode(
    execution_mode: str,
    expected_status: str,
    has_reservation: bool,
) -> None:
    client = TestClient(app)
    payload = {
        **make_mission_payload(
            participant_ids=make_existing_participant_ids(client)
        ),
        "execution_mode": execution_mode,
    }
    created = client.post("/missions", json=payload)

    response = client.post(
        f"/missions/{created.json()['id']}/run",
        headers={"Idempotency-Key": f"run-{execution_mode}"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == expected_status
    assert (response.json()["reservation_id"] is not None) is has_reservation
    assert response.json()["best_option"]["train_number"] == "001A"
    assert response.json()["execution_attempts"] == 0


@pytest.mark.parametrize("path_suffix", ["run", "confirm"])
def test_execution_commands_require_idempotency_key(path_suffix: str) -> None:
    client = TestClient(app)

    response = client.post(f"/missions/{uuid4()}/{path_suffix}")

    assert response.status_code == 422


def test_post_mission_run_replays_completed_idempotent_command() -> None:
    client = TestClient(app)
    mission_id = create_requires_confirmation_mission_with_key(
        client,
        "run-idempotency-key",
    )

    response = client.post(
        f"/missions/{mission_id}/run",
        headers={"Idempotency-Key": "run-idempotency-key"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "requires_confirmation"


def create_requires_confirmation_mission_with_key(
    client: TestClient,
    key: str,
) -> str:
    identity_ids = make_existing_participant_ids(client, count=4)
    payload = make_mission_payload(
        participant_ids=identity_ids,
        passengers_count=4,
        provider="mock_train",
    )
    payload["constraints"] = {
        "from_city": "Moscow",
        "to_city": "Saint Petersburg",
        "travel_date": "2026-08-01",
        "passengers_count": 4,
        "must_be_same_compartment": True,
        "min_lower_berths": 2,
        "max_total_price": 30000,
        "avoid_toilet": True,
    }
    created = client.post("/missions", json=payload)
    mission_id = created.json()["id"]
    response = client.post(
        f"/missions/{mission_id}/run",
        headers={"Idempotency-Key": key},
    )
    assert response.status_code == 200
    return str(mission_id)


def test_post_scheduled_mission_run_before_time_returns_409() -> None:
    client = TestClient(app)
    identity_ids = make_existing_participant_ids(client, count=4)
    scheduled_at = datetime.now(UTC) + timedelta(days=1)
    mission_payload = make_mission_payload(
        participant_ids=identity_ids,
        passengers_count=4,
        provider="mock_train",
    )
    mission_payload["constraints"] = {
        "from_city": "Moscow",
        "to_city": "Saint Petersburg",
        "travel_date": "2026-08-01",
        "passengers_count": 4,
        "must_be_same_compartment": True,
        "min_lower_berths": 2,
        "max_total_price": 30000,
        "avoid_toilet": True,
    }
    mission_payload["scheduled_at"] = scheduled_at.isoformat()
    create_response = client.post("/missions", json=mission_payload)
    mission_id = create_response.json()["id"]

    response = client.post(
        f"/missions/{mission_id}/run",
        headers={"Idempotency-Key": "run-before-schedule-key"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Mission is scheduled for a future time"


def test_post_mission_run_twice_returns_409() -> None:
    client = TestClient(app)
    mission_id = create_requires_confirmation_mission(client)

    response = client.post(
        f"/missions/{mission_id}/run",
        headers={"Idempotency-Key": "run-second-key"},
    )

    assert response.status_code == 409
    assert "requires_confirmation" in response.json()["detail"]


def test_post_unknown_mission_run_returns_404() -> None:
    client = TestClient(app)

    response = client.post(
        f"/missions/{uuid4()}/run",
        headers={"Idempotency-Key": "run-unknown-key"},
    )

    assert response.status_code == 404


def create_requires_confirmation_mission(client: TestClient) -> str:
    identity_payloads = [make_identity_payload() for _ in range(4)]
    identity_ids: list[str] = []
    for identity_payload in identity_payloads:
        response = client.post("/identities", json=identity_payload)
        identity_ids.append(response.json()["id"])
    mission_payload = make_mission_payload(
        participant_ids=identity_ids,
        passengers_count=4,
        provider="mock_train",
    )
    mission_payload["constraints"] = {
        "from_city": "Moscow",
        "to_city": "Saint Petersburg",
        "travel_date": "2026-08-01",
        "passengers_count": 4,
        "must_be_same_compartment": True,
        "min_lower_berths": 2,
        "max_total_price": 30000,
        "avoid_toilet": True,
    }
    create_response = client.post("/missions", json=mission_payload)
    mission_id = create_response.json()["id"]
    client.post(
        f"/missions/{mission_id}/run",
        headers={"Idempotency-Key": "initial-run-key"},
    )
    return str(mission_id)


def test_post_mission_confirm_returns_completed() -> None:
    client = TestClient(app)
    mission_id = create_requires_confirmation_mission(client)

    response = client.post(
        f"/missions/{mission_id}/confirm",
        headers={"Idempotency-Key": "confirm-success-key"},
    )
    event_types = [event["type"] for event in response.json()["execution_log"]]

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert "mission_confirmed" in event_types
    assert "mission_completed" in event_types


def test_post_mission_confirm_before_run_returns_409() -> None:
    client = TestClient(app)
    payload = make_mission_payload(
        participant_ids=make_existing_participant_ids(client)
    )
    create_response = client.post("/missions", json=payload)
    mission_id = create_response.json()["id"]

    response = client.post(
        f"/missions/{mission_id}/confirm",
        headers={"Idempotency-Key": "confirm-before-run-key"},
    )

    assert response.status_code == 409


def test_failed_confirmation_releases_idempotency_key_for_retry() -> None:
    client = TestClient(app)
    payload = make_mission_payload(
        participant_ids=make_existing_participant_ids(client)
    )
    create_response = client.post("/missions", json=payload)
    mission_id = create_response.json()["id"]
    headers = {"Idempotency-Key": "confirm-retry-key"}

    first_confirmation = client.post(
        f"/missions/{mission_id}/confirm",
        headers=headers,
    )
    run_response = client.post(
        f"/missions/{mission_id}/run",
        headers={"Idempotency-Key": "run-after-confirm-failure-key"},
    )
    retry_confirmation = client.post(
        f"/missions/{mission_id}/confirm",
        headers=headers,
    )
    replay_confirmation = client.post(
        f"/missions/{mission_id}/confirm",
        headers=headers,
    )

    assert first_confirmation.status_code == 409
    assert run_response.status_code == 200
    assert retry_confirmation.status_code == 200
    assert retry_confirmation.json()["status"] == "completed"
    assert replay_confirmation.status_code == 200
    assert replay_confirmation.json()["status"] == "completed"


def test_post_unknown_mission_confirm_returns_404() -> None:
    client = TestClient(app)

    response = client.post(
        f"/missions/{uuid4()}/confirm",
        headers={"Idempotency-Key": "confirm-unknown-key"},
    )

    assert response.status_code == 404


def test_post_mission_confirm_twice_returns_409() -> None:
    client = TestClient(app)
    mission_id = create_requires_confirmation_mission(client)
    client.post(
        f"/missions/{mission_id}/confirm",
        headers={"Idempotency-Key": "confirm-first-key"},
    )

    response = client.post(
        f"/missions/{mission_id}/confirm",
        headers={"Idempotency-Key": "confirm-second-key"},
    )

    assert response.status_code == 409


def test_post_mission_cancel_returns_cancelled_and_replays_idempotently() -> None:
    client = TestClient(app)
    payload = make_mission_payload(
        participant_ids=make_existing_participant_ids(client)
    )
    mission_id = client.post("/missions", json=payload).json()["id"]
    headers = {"Idempotency-Key": "cancel-created-key"}

    first_response = client.post(
        f"/missions/{mission_id}/cancel",
        headers=headers,
    )
    second_response = client.post(
        f"/missions/{mission_id}/cancel",
        headers=headers,
    )

    assert first_response.status_code == 200
    assert first_response.json()["status"] == "cancelled"
    assert second_response.status_code == 200
    assert second_response.json() == first_response.json()


def test_post_mission_cancel_cancels_provider_reservation() -> None:
    client = TestClient(app)
    mission_id = create_requires_confirmation_mission(client)

    response = client.post(
        f"/missions/{mission_id}/cancel",
        headers={"Idempotency-Key": "cancel-reservation-key"},
    )
    event_types = [event["type"] for event in response.json()["execution_log"]]

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert event_types[-3:] == [
        "cancellation_started",
        "cancellation_succeeded",
        "mission_cancelled",
    ]


def test_put_mission_schedule_moves_created_mission_to_waiting() -> None:
    client = TestClient(app)
    payload = make_mission_payload(
        participant_ids=make_existing_participant_ids(client)
    )
    mission_id = client.post("/missions", json=payload).json()["id"]

    response = client.put(
        f"/missions/{mission_id}/schedule",
        json={"scheduled_at": "2030-08-01T10:00:00Z"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "waiting"
    assert response.json()["scheduled_at"] == "2030-08-01T10:00:00Z"
    assert response.json()["execution_log"][-1]["type"] == "mission_scheduled"


def test_put_mission_schedule_null_unschedules_waiting_mission() -> None:
    client = TestClient(app)
    payload = make_mission_payload(
        participant_ids=make_existing_participant_ids(client)
    )
    mission_id = client.post("/missions", json=payload).json()["id"]
    client.put(
        f"/missions/{mission_id}/schedule",
        json={"scheduled_at": "2030-08-01T10:00:00Z"},
    )

    response = client.put(
        f"/missions/{mission_id}/schedule",
        json={"scheduled_at": None},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "created"
    assert response.json()["scheduled_at"] is None
    assert response.json()["execution_log"][-1]["type"] == "mission_unscheduled"


def test_mission_mutation_rejects_stale_if_match_version() -> None:
    client = TestClient(app)
    payload = make_mission_payload(
        participant_ids=make_existing_participant_ids(client)
    )
    mission_id = client.post("/missions", json=payload).json()["id"]
    client.put(
        f"/missions/{mission_id}/schedule",
        json={"scheduled_at": "2030-08-01T10:00:00Z"},
    )

    response = client.put(
        f"/missions/{mission_id}/schedule",
        json={"scheduled_at": "2030-08-02T10:00:00Z"},
        headers={"If-Match": '"0"'},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "mission_version_conflict",
        "message": (
            "Mission changed since the requested version. Reload it and try again."
        ),
        "details": {"current_version": 1, "expected_version": 0},
    }


def test_mission_mutation_rejects_invalid_if_match_version() -> None:
    client = TestClient(app)
    payload = make_mission_payload(
        participant_ids=make_existing_participant_ids(client)
    )
    mission_id = client.post("/missions", json=payload).json()["id"]

    response = client.put(
        f"/missions/{mission_id}/schedule",
        json={"scheduled_at": "2030-08-01T10:00:00Z"},
        headers={"If-Match": "not-a-version"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_mission_version"


def test_mission_resource_mutations_expose_current_version_as_etag() -> None:
    client = TestClient(app)
    payload = make_mission_payload(
        participant_ids=make_existing_participant_ids(client)
    )

    created = client.post("/missions", json=payload)
    mission_id = created.json()["id"]
    fetched = client.get(f"/missions/{mission_id}")
    scheduled = client.put(
        f"/missions/{mission_id}/schedule",
        json={"scheduled_at": "2030-08-01T10:00:00Z"},
        headers={"If-Match": created.headers["etag"]},
    )
    unchanged = client.put(
        f"/missions/{mission_id}/schedule",
        json={"scheduled_at": "2030-08-01T10:00:00Z"},
        headers={"If-Match": scheduled.headers["etag"]},
    )
    cancelled = client.post(
        f"/missions/{mission_id}/cancel",
        headers={
            "Idempotency-Key": "etag-cancel-key",
            "If-Match": unchanged.headers["etag"],
        },
    )
    replayed = client.post(
        f"/missions/{mission_id}/cancel",
        headers={
            "Idempotency-Key": "etag-cancel-key",
            "If-Match": cancelled.headers["etag"],
        },
    )

    assert created.headers["etag"] == '"0"'
    assert fetched.headers["etag"] == '"0"'
    assert scheduled.headers["etag"] == '"1"'
    assert unchanged.headers["etag"] == '"1"'
    assert cancelled.headers["etag"] == '"2"'
    assert replayed.headers["etag"] == '"2"'


def test_mission_execution_mutations_expose_current_version_as_etag() -> None:
    client = TestClient(app)
    payload = make_mission_payload(
        participant_ids=make_existing_participant_ids(client)
    )

    created = client.post("/missions", json=payload)
    mission_id = created.json()["id"]
    run = client.post(
        f"/missions/{mission_id}/run",
        headers={
            "Idempotency-Key": "etag-run-key",
            "If-Match": created.headers["etag"],
        },
    )
    run_replay = client.post(
        f"/missions/{mission_id}/run",
        headers={
            "Idempotency-Key": "etag-run-key",
            "If-Match": run.headers["etag"],
        },
    )
    confirmed = client.post(
        f"/missions/{mission_id}/confirm",
        headers={
            "Idempotency-Key": "etag-confirm-key",
            "If-Match": run_replay.headers["etag"],
        },
    )

    assert run.status_code == 200
    assert run.headers["etag"] == f'"{run.json()["last_event_sequence"]}"'
    assert run_replay.headers["etag"] == run.headers["etag"]
    assert confirmed.status_code == 200
    assert confirmed.headers["etag"] == (
        f'"{confirmed.json()["last_event_sequence"]}"'
    )


def test_get_mission_events_returns_bounded_canonical_history() -> None:
    client = TestClient(app)
    payload = make_mission_payload(
        participant_ids=make_existing_participant_ids(client)
    )
    mission_id = client.post("/missions", json=payload).json()["id"]
    client.put(
        f"/missions/{mission_id}/schedule",
        json={"scheduled_at": "2030-08-01T10:00:00Z"},
    )

    response = client.get(
        f"/missions/{mission_id}/events",
        params={"after_sequence": 0, "limit": 1},
    )

    assert response.status_code == 200
    assert response.json()["after_sequence"] == 0
    assert response.json()["latest_sequence"] == 1
    assert response.json()["has_more"] is False
    assert [event["type"] for event in response.json()["items"]] == [
        "mission_scheduled"
    ]


def test_post_completed_mission_run_returns_409() -> None:
    client = TestClient(app)
    mission_id = create_requires_confirmation_mission(client)
    client.post(
        f"/missions/{mission_id}/confirm",
        headers={"Idempotency-Key": "confirm-complete-key"},
    )

    response = client.post(
        f"/missions/{mission_id}/run",
        headers={"Idempotency-Key": "run-after-complete-key"},
    )

    assert response.status_code == 409
    assert "completed" in response.json()["detail"]

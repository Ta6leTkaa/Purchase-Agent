import asyncio
from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.dependencies import identity_repository
from app.main import app


@pytest.fixture(autouse=True)
def clear_repository() -> Iterator[None]:
    asyncio.run(identity_repository.clear())
    yield
    asyncio.run(identity_repository.clear())


def make_identity_payload() -> dict[str, object]:
    return {
        "display_name": "Ivan Petrov",
        "first_name": "Ivan",
        "last_name": "Petrov",
        "birth_date": "1990-01-01",
        "documents": [
            {
                "type": "internal_passport",
                "number": "1234567890",
            }
        ],
    }


def test_post_identities_creates_identity() -> None:
    client = TestClient(app)
    payload = make_identity_payload()

    response = client.post("/identities", json=payload)

    assert response.status_code == 200
    assert response.json()["id"] is not None
    assert response.json()["documents"][0]["number"] == "1234567890"


def test_post_identities_without_id_generates_uuid() -> None:
    client = TestClient(app)
    payload = make_identity_payload()

    response = client.post("/identities", json=payload)

    assert response.status_code == 200
    assert UUID(response.json()["id"])


def test_post_identities_with_id_returns_422() -> None:
    client = TestClient(app)
    payload = {
        **make_identity_payload(),
        "id": str(uuid4()),
    }

    response = client.post("/identities", json=payload)

    assert response.status_code == 422


def test_get_identities_returns_created_identity() -> None:
    client = TestClient(app)
    payload = make_identity_payload()
    create_response = client.post("/identities", json=payload)

    response = client.get("/identities")

    assert response.status_code == 200
    assert response.json()[0]["id"] == create_response.json()["id"]


def test_get_identities_searches_names_case_insensitively() -> None:
    client = TestClient(app)
    ivan = client.post("/identities", json=make_identity_payload())
    client.post(
        "/identities",
        json={
            **make_identity_payload(),
            "display_name": "Anna Sidorova",
            "first_name": "Anna",
            "last_name": "Sidorova",
        },
    )

    response = client.get("/identities", params={"q": "pEtRoV"})

    assert response.status_code == 200
    assert [identity["id"] for identity in response.json()] == [
        ivan.json()["id"]
    ]


def test_get_identities_applies_limit() -> None:
    client = TestClient(app)
    client.post("/identities", json=make_identity_payload())
    client.post(
        "/identities",
        json={
            **make_identity_payload(),
            "display_name": "Anna Sidorova",
            "first_name": "Anna",
            "last_name": "Sidorova",
        },
    )

    response = client.get("/identities", params={"limit": 1})

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_get_identity_summaries_returns_only_public_listing_fields() -> None:
    client = TestClient(app)
    created = client.post("/identities", json=make_identity_payload()).json()

    response = client.get(
        "/identities/summaries",
        params={"q": "pEtRoV", "limit": 1},
    )

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": created["id"],
            "display_name": "Ivan Petrov",
        }
    ]


def test_get_identity_summary_pages_use_exclusive_cursor() -> None:
    client = TestClient(app)
    created_ids = {
        client.post(
            "/identities",
            json={
                **make_identity_payload(),
                "display_name": f"Person {index}",
            },
        ).json()["id"]
        for index in range(3)
    }

    first = client.get("/identities/summaries/page", params={"limit": 2})

    assert first.status_code == 200
    assert first.json()["has_more"] is True
    assert first.json()["next_cursor"] is not None
    second = client.get(
        "/identities/summaries/page",
        params={"limit": 2, "cursor": first.json()["next_cursor"]},
    )
    assert second.status_code == 200
    assert second.json()["has_more"] is False
    assert second.json()["next_cursor"] is None
    returned_ids = {
        item["id"] for item in first.json()["items"] + second.json()["items"]
    }
    assert returned_ids == created_ids


def test_get_identity_summary_page_rejects_invalid_cursor() -> None:
    response = TestClient(app).get(
        "/identities/summaries/page",
        params={"cursor": "invalid"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_identity_cursor"


@pytest.mark.parametrize(
    "params",
    [
        {"q": ""},
        {"q": "   "},
        {"limit": 0},
        {"limit": 501},
    ],
)
def test_get_identities_rejects_invalid_search(
    params: dict[str, object],
) -> None:
    response = TestClient(app).get("/identities", params=params)

    assert response.status_code == 422


def test_get_identity_by_id_returns_created_identity() -> None:
    client = TestClient(app)
    payload = make_identity_payload()
    create_response = client.post("/identities", json=payload)
    identity_id = create_response.json()["id"]

    response = client.get(f"/identities/{identity_id}")

    assert response.status_code == 200
    assert response.json()["id"] == identity_id


def test_get_unknown_identity_returns_404() -> None:
    client = TestClient(app)

    response = client.get(f"/identities/{uuid4()}")

    assert response.status_code == 404


def test_put_identity_preferences_updates_notification_settings() -> None:
    client = TestClient(app)
    create_response = client.post("/identities", json=make_identity_payload())
    created = create_response.json()

    response = client.put(
        f"/identities/{created['id']}/preferences",
        json={
            "preferences": {
                "notifications": {
                    "enabled": True,
                    "channels": ["telegram", "webhook"],
                    "external_recipient_id": "chat:12345",
                }
            }
        },
        headers={"If-Match": create_response.headers["etag"]},
    )

    assert response.status_code == 200
    notifications = response.json()["preferences"]["notifications"]
    assert notifications["enabled"] is True
    assert set(notifications["channels"]) == {"telegram", "webhook"}
    assert notifications["external_recipient_id"] == "chat:12345"
    assert response.headers["etag"] == '"1"'
    assert response.json()["version"] == 1


def test_patch_identity_updates_profile_and_preserves_preferences() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/identities",
        json=make_identity_payload(),
    )
    created = create_response.json()

    response = client.patch(
        f"/identities/{created['id']}",
        json={
            "display_name": "  Anna Sidorova  ",
            "first_name": "Anna",
            "last_name": "Sidorova",
            "documents": [
                {
                    "type": "international_passport",
                    "number": "  987654321  ",
                    "expires_at": "2035-01-01",
                }
            ],
        },
        headers={"If-Match": create_response.headers["etag"]},
    )

    assert response.status_code == 200
    updated = response.json()
    assert updated["display_name"] == "Anna Sidorova"
    assert updated["birth_date"] == created["birth_date"]
    assert updated["preferences"] == created["preferences"]
    assert updated["documents"][0]["number"] == "987654321"
    assert updated["documents"][0]["id"] != created["documents"][0]["id"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"display_name": "   "},
        {"first_name": None},
        {"documents": None},
        {"unknown": "value"},
    ],
)
def test_patch_identity_rejects_invalid_changes(
    payload: dict[str, object],
) -> None:
    client = TestClient(app)
    identity_id = client.post(
        "/identities",
        json=make_identity_payload(),
    ).json()["id"]

    response = client.patch(f"/identities/{identity_id}", json=payload)

    assert response.status_code == 422


def test_patch_unknown_identity_returns_404() -> None:
    response = TestClient(app).patch(
        f"/identities/{uuid4()}",
        json={"display_name": "Unknown"},
    )

    assert response.status_code == 404


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("display_name", "   "),
        ("first_name", ""),
        ("last_name", "   "),
    ],
)
def test_create_identity_rejects_blank_names(field: str, value: str) -> None:
    response = TestClient(app).post(
        "/identities",
        json={**make_identity_payload(), field: value},
    )

    assert response.status_code == 422


def test_delete_identity_removes_unreferenced_identity() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/identities",
        json=make_identity_payload(),
    )
    identity_id = create_response.json()["id"]

    response = client.delete(
        f"/identities/{identity_id}",
        headers={"If-Match": create_response.headers["etag"]},
    )

    assert response.status_code == 204
    assert response.content == b""
    assert client.get(f"/identities/{identity_id}").status_code == 404


def test_delete_identity_rejects_identity_used_by_mission() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/identities",
        json=make_identity_payload(),
    )
    identity_id = create_response.json()["id"]
    mission_response = client.post(
        "/missions",
        json={
            "type": "train_trip",
            "title": "Moscow to Saint Petersburg",
            "participant_ids": [identity_id],
            "provider": "mock_train",
            "payload": {
                "origin": "Moscow",
                "destination": "Saint Petersburg",
                "departure_date": "2026-08-10",
            },
            "constraints": {
                "from_city": "Moscow",
                "to_city": "Saint Petersburg",
                "travel_date": "2026-08-10",
                "passengers_count": 1,
            },
        },
    )
    assert mission_response.status_code == 200

    response = client.delete(
        f"/identities/{identity_id}",
        headers={"If-Match": create_response.headers["etag"]},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "identity_in_use"
    assert client.get(f"/identities/{identity_id}").status_code == 200


def test_delete_unknown_identity_returns_404() -> None:
    response = TestClient(app).delete(f"/identities/{uuid4()}")

    assert response.status_code == 404


def test_identity_etag_prevents_lost_updates() -> None:
    client = TestClient(app)
    created = client.post("/identities", json=make_identity_payload())
    identity_id = created.json()["id"]

    assert created.headers["etag"] == '"0"'
    first_update = client.patch(
        f"/identities/{identity_id}",
        json={"display_name": "First update"},
        headers={"If-Match": created.headers["etag"]},
    )
    stale_update = client.patch(
        f"/identities/{identity_id}",
        json={"display_name": "Stale update"},
        headers={"If-Match": created.headers["etag"]},
    )

    assert first_update.status_code == 200
    assert first_update.headers["etag"] == '"1"'
    assert first_update.json()["version"] == 1
    assert stale_update.status_code == 412
    assert stale_update.json()["detail"]["code"] == "identity_version_conflict"
    loaded = client.get(f"/identities/{identity_id}")
    assert loaded.json()["display_name"] == "First update"
    assert loaded.headers["etag"] == '"1"'


def test_identity_mutation_requires_if_match() -> None:
    client = TestClient(app)
    created = client.post("/identities", json=make_identity_payload()).json()

    response = client.patch(
        f"/identities/{created['id']}",
        json={"display_name": "Changed"},
    )

    assert response.status_code == 428
    assert response.json()["detail"]["code"] == "identity_version_required"

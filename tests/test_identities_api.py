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
    created = client.post("/identities", json=make_identity_payload()).json()

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
    )

    assert response.status_code == 200
    notifications = response.json()["preferences"]["notifications"]
    assert notifications["enabled"] is True
    assert set(notifications["channels"]) == {"telegram", "webhook"}
    assert notifications["external_recipient_id"] == "chat:12345"

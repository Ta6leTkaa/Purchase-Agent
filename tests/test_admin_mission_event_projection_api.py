from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import settings
from app.dependencies import get_mission_event_projection_verifier
from app.main import app
from app.services.mission_errors import MissionNotFoundError
from app.services.mission_event_projection import (
    MissionEventProjectionVerification,
    MissionEventProjectionVerificationStatus,
)

ADMIN_KEY = "test-admin-key"
ENDPOINT = "/admin/missions/{mission_id}/event-projection/verification"


@pytest.fixture(autouse=True)
def reset_dependencies() -> Iterator[None]:
    original_admin_api_key = settings.admin_api_key
    settings.admin_api_key = SecretStr(ADMIN_KEY)
    yield
    app.dependency_overrides.clear()
    settings.admin_api_key = original_admin_api_key


def test_event_projection_verification_is_read_only_admin_diagnostic() -> None:
    mission_id = uuid4()
    verifier = StubVerifier(
        MissionEventProjectionVerification(
            mission_id=mission_id,
            status=MissionEventProjectionVerificationStatus.CONSISTENT,
            canonical_event_count=2,
            projection_event_count=2,
            missing_projection_sequences=(),
            unexpected_projection_sequences=(),
            mismatches=(),
        )
    )
    app.dependency_overrides[get_mission_event_projection_verifier] = lambda: verifier

    response = TestClient(app).get(
        ENDPOINT.format(mission_id=mission_id),
        headers={"X-Admin-API-Key": ADMIN_KEY},
    )

    assert response.status_code == 200
    assert response.json() == {
        "mission_id": str(mission_id),
        "status": "consistent",
        "canonical_event_count": 2,
        "projection_event_count": 2,
        "missing_projection_sequences": [],
        "unexpected_projection_sequences": [],
        "mismatches": [],
    }
    assert verifier.calls == [mission_id]


def test_event_projection_verification_returns_404_for_unknown_mission() -> None:
    app.dependency_overrides[get_mission_event_projection_verifier] = (
        lambda: MissingVerifier()
    )

    response = TestClient(app).get(
        ENDPOINT.format(mission_id=uuid4()),
        headers={"X-Admin-API-Key": ADMIN_KEY},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Mission not found"}


@pytest.mark.parametrize(
    ("headers", "status_code"),
    [({}, 401), ({"X-Admin-API-Key": "wrong-key"}, 403)],
)
def test_event_projection_verification_requires_valid_admin_key(
    headers: dict[str, str],
    status_code: int,
) -> None:
    response = TestClient(app).get(
        ENDPOINT.format(mission_id=uuid4()),
        headers=headers,
    )

    assert response.status_code == status_code


class StubVerifier:
    def __init__(self, result: MissionEventProjectionVerification) -> None:
        self._result = result
        self.calls: list[object] = []

    async def execute(self, mission_id: object) -> MissionEventProjectionVerification:
        self.calls.append(mission_id)
        return self._result


class MissingVerifier:
    async def execute(self, mission_id: object) -> MissionEventProjectionVerification:
        raise MissionNotFoundError

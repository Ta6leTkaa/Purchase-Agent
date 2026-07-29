from fastapi.testclient import TestClient

from app.main import app
from app.repositories.sqlalchemy.mission import MissionEventSequenceConflictError


def test_mission_event_sequence_conflict_has_stable_http_response() -> None:
    @app.get("/_test-mission-version-conflict")
    async def raise_conflict() -> None:
        raise MissionEventSequenceConflictError

    try:
        response = TestClient(app, raise_server_exceptions=False).get(
            "/_test-mission-version-conflict"
        )
    finally:
        app.router.routes.pop()

    assert response.status_code == 409
    assert response.json() == {
        "detail": {
            "code": "mission_version_conflict",
            "message": (
                "Mission changed while this command was being processed. "
                "Reload it and try again."
            ),
        }
    }

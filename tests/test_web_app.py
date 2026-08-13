from fastapi.testclient import TestClient

from app.main import app


def test_root_redirects_to_web_app() -> None:
    response = TestClient(app).get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/app"


def test_web_app_is_available() -> None:
    response = TestClient(app).get("/app")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    assert "Purchase Agent" in response.text
    assert "passengerForm" in response.text
    assert "Мои поездки" in response.text
    assert "Пассажиры" in response.text
    assert "Новая поездка" in response.text
    assert "/app/app.css?v=20260813-2" in response.text
    assert "/app/app.js?v=20260813-2" in response.text


def test_web_assets_are_served_with_explicit_types() -> None:
    client = TestClient(app)
    styles = client.get("/app/app.css")
    script = client.get("/app/app.js")
    assert styles.status_code == 200
    assert styles.headers["content-type"].startswith("text/css")
    assert styles.headers["cache-control"] == "no-store"
    assert script.status_code == 200
    assert script.headers["content-type"].startswith("text/javascript")
    assert script.headers["cache-control"] == "no-store"
    assert "X-API-Key" in script.text
    assert 'api("/missions?limit=100")' in script.text
    assert 'api("/identities?limit=100")' in script.text
    assert "performMissionAction" in script.text
    assert "loadActivity" in script.text


def test_web_routes_are_not_exposed_in_openapi() -> None:
    paths = TestClient(app).get("/openapi.json").json()["paths"]
    assert "/app" not in paths
    assert "/app/app.css" not in paths
    assert "/app/app.js" not in paths

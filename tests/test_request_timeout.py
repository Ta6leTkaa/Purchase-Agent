import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse
from starlette.types import Message, Receive, Scope, Send

from app.api.middleware.request_timeout import RequestTimeoutMiddleware
from app.core.config import Settings
from app.main import create_app


def test_timeout_response_keeps_cors_and_request_id() -> None:
    config = Settings.model_construct(
        cors_allowed_origins=["http://localhost:3000"],
        request_timeout_seconds=0.01,
    )
    application = create_app(config)

    @application.get("/slow-test")
    async def slow_endpoint() -> dict[str, str]:
        await asyncio.sleep(1)
        return {"status": "unexpected"}

    response = TestClient(application).get(
        "/slow-test",
        headers={
            "Origin": "http://localhost:3000",
            "X-Request-ID": "timed-out-request",
        },
    )

    assert response.status_code == 504
    assert response.json()["detail"]["code"] == "request_timeout"
    assert response.headers["x-request-id"] == "timed-out-request"
    assert response.headers["access-control-allow-origin"] == (
        "http://localhost:3000"
    )


@pytest.mark.asyncio
async def test_request_timeout_cancels_work_and_returns_504() -> None:
    cancelled = False

    async def slow_app(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal cancelled
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            cancelled = True
            raise

    middleware = RequestTimeoutMiddleware(slow_app, timeout_seconds=0.01)
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b""}

    async def send(message: Message) -> None:
        sent.append(message)

    await middleware(_http_scope(), receive, send)

    assert cancelled
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 504
    assert b'"code":"request_timeout"' in sent[1]["body"]
    assert b'"timeout_seconds":0.01' in sent[1]["body"]


@pytest.mark.asyncio
async def test_request_finishing_before_deadline_is_unchanged() -> None:
    async def fast_app(scope: Scope, receive: Receive, send: Send) -> None:
        await JSONResponse({"status": "ok"})(scope, receive, send)

    middleware = RequestTimeoutMiddleware(fast_app, timeout_seconds=1)
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b""}

    async def send(message: Message) -> None:
        sent.append(message)

    await middleware(_http_scope(), receive, send)

    assert sent[0]["status"] == 200
    assert b'"status":"ok"' in sent[1]["body"]


@pytest.mark.asyncio
async def test_application_timeout_error_is_not_misreported_as_deadline() -> None:
    async def failing_app(scope: Scope, receive: Receive, send: Send) -> None:
        raise TimeoutError("downstream operation failed")

    middleware = RequestTimeoutMiddleware(failing_app, timeout_seconds=1)

    async def receive() -> Message:
        return {"type": "http.request", "body": b""}

    async def send(message: Message) -> None:
        return None

    with pytest.raises(TimeoutError, match="downstream operation failed"):
        await middleware(_http_scope(), receive, send)


@pytest.mark.asyncio
async def test_non_http_scope_is_not_timed_out() -> None:
    called = False

    async def lifespan_app(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal called
        called = True

    middleware = RequestTimeoutMiddleware(lifespan_app, timeout_seconds=0.01)

    async def receive() -> Message:
        return {"type": "lifespan.startup"}

    async def send(message: Message) -> None:
        return None

    await middleware(
        {"type": "lifespan", "asgi": {"version": "3.0"}, "state": {}},
        receive,
        send,
    )

    assert called


def _http_scope() -> Scope:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/slow",
        "raw_path": b"/slow",
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "server": ("test", 80),
        "client": ("client", 123),
        "state": {},
    }

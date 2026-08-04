from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from starlette.types import Message, Receive, Scope, Send

from app.api.middleware.request_body_limit import RequestBodyLimitMiddleware
from app.core.config import Settings
from app.main import create_app


def test_oversized_declared_body_is_rejected_before_routing() -> None:
    application = create_app(
        Settings(
            cors_allowed_origins=["http://localhost:3000"],
            max_request_body_bytes=1024,
        )
    )

    response = TestClient(application).post(
        "/identities",
        content=b"x" * 1025,
        headers={
            "Content-Type": "application/json",
            "Origin": "http://localhost:3000",
            "X-Request-ID": "oversized-request",
        },
    )

    assert response.status_code == 413
    assert response.json() == {
        "detail": {
            "code": "request_body_too_large",
            "message": "Request body exceeds the configured limit.",
            "max_bytes": 1024,
        }
    }
    assert response.headers["x-request-id"] == "oversized-request"
    assert response.headers["access-control-allow-origin"] == (
        "http://localhost:3000"
    )
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


def test_body_at_configured_limit_reaches_routing() -> None:
    response = TestClient(
        create_app(Settings(max_request_body_bytes=1024))
    ).post(
        "/missing-resource",
        content=b"x" * 1024,
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_incremental_body_is_rejected_without_content_length() -> None:
    downstream_called = False

    async def downstream(
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        nonlocal downstream_called
        downstream_called = True

    middleware = RequestBodyLimitMiddleware(downstream, max_body_bytes=1024)
    messages = _messages(
        {"type": "http.request", "body": b"a" * 600, "more_body": True},
        {"type": "http.request", "body": b"b" * 600, "more_body": False},
    )
    sent: list[Message] = []

    async def capture(message: Message) -> None:
        sent.append(message)

    await middleware(
        _http_scope(headers=[]),
        messages.__anext__,
        capture,
    )

    assert not downstream_called
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_buffered_body_is_replayed_unchanged() -> None:
    received_body = b""

    async def downstream(
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        nonlocal received_body
        message = await receive()
        received_body = message["body"]

    middleware = RequestBodyLimitMiddleware(downstream, max_body_bytes=1024)
    messages = _messages(
        {"type": "http.request", "body": b"first-", "more_body": True},
        {"type": "http.request", "body": b"second", "more_body": False},
    )

    async def discard(message: Message) -> None:
        return None

    await middleware(
        _http_scope(headers=[]),
        messages.__anext__,
        discard,
    )

    assert received_body == b"first-second"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        [(b"content-length", b"invalid")],
        [(b"content-length", b"-1")],
        [(b"content-length", b"1"), (b"content-length", b"1")],
    ],
)
async def test_ambiguous_content_length_is_rejected(
    headers: list[tuple[bytes, bytes]],
) -> None:
    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        raise AssertionError("invalid request must not reach the application")

    middleware = RequestBodyLimitMiddleware(downstream, max_body_bytes=1024)
    messages = _messages({"type": "http.request", "body": b""})
    sent: list[Message] = []

    async def capture(message: Message) -> None:
        sent.append(message)

    await middleware(_http_scope(headers=headers), messages.__anext__, capture)

    assert sent[0]["status"] == 400


async def _messages(*messages: Message) -> AsyncIterator[Message]:
    for message in messages:
        yield message


def _http_scope(*, headers: list[tuple[bytes, bytes]]) -> Scope:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/test",
        "raw_path": b"/test",
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "server": ("test", 80),
        "client": ("client", 123),
        "state": {},
    }

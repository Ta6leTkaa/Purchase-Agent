from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        self._app = app
        self._max_body_bytes = max_body_bytes

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        content_length = _content_length(scope)
        if content_length is None:
            pass
        elif content_length < 0:
            await self._invalid_content_length(scope, receive, send)
            return
        elif content_length > self._max_body_bytes:
            await self._body_too_large(scope, receive, send)
            return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            body.extend(chunk)
            if len(body) > self._max_body_bytes:
                await self._body_too_large(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        delivered = False

        async def replay_receive() -> Message:
            nonlocal delivered
            if delivered:
                return {"type": "http.disconnect"}
            delivered = True
            return {"type": "http.request", "body": bytes(body)}

        await self._app(scope, replay_receive, send)

    async def _body_too_large(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        response = JSONResponse(
            status_code=413,
            content={
                "detail": {
                    "code": "request_body_too_large",
                    "message": "Request body exceeds the configured limit.",
                    "max_bytes": self._max_body_bytes,
                }
            },
        )
        await response(scope, receive, send)

    @staticmethod
    async def _invalid_content_length(
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        response = JSONResponse(
            status_code=400,
            content={"detail": "Content-Length must be a non-negative integer"},
        )
        await response(scope, receive, send)


def _content_length(scope: Scope) -> int | None:
    content_lengths = [
        value
        for name, value in scope["headers"]
        if name.lower() == b"content-length"
    ]
    if not content_lengths:
        return None
    if len(content_lengths) != 1 or not content_lengths[0].isdigit():
        return -1
    return int(content_lengths[0])

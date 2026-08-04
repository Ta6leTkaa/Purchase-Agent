from starlette.types import ASGIApp, Message, Receive, Scope, Send

_SECURITY_HEADERS = {
    b"permissions-policy": b"camera=(), geolocation=(), microphone=()",
    b"referrer-policy": b"no-referrer",
    b"x-content-type-options": b"nosniff",
    b"x-frame-options": b"DENY",
}


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] != "http.response.start":
                await send(message)
                return
            headers = [
                (name, value)
                for name, value in message.get("headers", [])
                if name.lower() not in _SECURITY_HEADERS
            ]
            headers.extend(_SECURITY_HEADERS.items())
            status = message["status"]
            if status >= 400 and not any(
                name.lower() == b"cache-control" for name, _ in headers
            ):
                headers.append((b"cache-control", b"no-store"))
            await send({**message, "headers": headers})

        await self._app(scope, receive, send_with_security_headers)

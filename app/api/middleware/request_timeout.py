import asyncio

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestTimeoutMiddleware:
    def __init__(self, app: ASGIApp, *, timeout_seconds: float) -> None:
        self._app = app
        self._timeout_seconds = timeout_seconds

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        response_started = False

        async def track_response(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        deadline = asyncio.timeout(self._timeout_seconds)
        try:
            async with deadline:
                await self._app(scope, receive, track_response)
        except TimeoutError:
            if not deadline.expired():
                raise
            if response_started:
                return
            response = JSONResponse(
                status_code=504,
                content={
                    "detail": {
                        "code": "request_timeout",
                        "message": "Request processing exceeded its deadline.",
                        "timeout_seconds": self._timeout_seconds,
                    }
                },
            )
            await response(scope, receive, send)

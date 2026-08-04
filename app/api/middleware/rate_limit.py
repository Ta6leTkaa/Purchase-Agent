import hashlib
import math
import time
from collections import OrderedDict, deque
from collections.abc import Callable

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RateLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        request_limit: int,
        window_seconds: float,
        max_clients: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if request_limit < 1:
            raise ValueError("request_limit must be at least one")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be greater than zero")
        if max_clients < 1:
            raise ValueError("max_clients must be at least one")
        self._app = app
        self._request_limit = request_limit
        self._window_seconds = window_seconds
        self._max_clients = max_clients
        self._clock = clock
        self._requests: OrderedDict[str, deque[float]] = OrderedDict()

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if not _should_limit(scope):
            await self._app(scope, receive, send)
            return

        now = self._clock()
        client_id = _client_id(scope)
        timestamps = self._requests.get(client_id)
        if timestamps is None:
            self._evict_client_if_full()
            timestamps = deque()
            self._requests[client_id] = timestamps
        else:
            self._requests.move_to_end(client_id)

        cutoff = now - self._window_seconds
        while timestamps and timestamps[0] <= cutoff:
            timestamps.popleft()

        if len(timestamps) >= self._request_limit:
            retry_after = max(1, math.ceil(timestamps[0] + self._window_seconds - now))
            response = JSONResponse(
                status_code=429,
                content={
                    "detail": {
                        "code": "rate_limit_exceeded",
                        "message": (
                            "Too many requests. Retry after the indicated delay."
                        ),
                        "retry_after_seconds": retry_after,
                    }
                },
                headers={
                    "Retry-After": str(retry_after),
                    "RateLimit-Limit": str(self._request_limit),
                    "RateLimit-Remaining": "0",
                    "RateLimit-Reset": str(retry_after),
                },
            )
            await response(scope, receive, send)
            return

        timestamps.append(now)
        remaining = self._request_limit - len(timestamps)
        reset_after = max(1, math.ceil(timestamps[0] + self._window_seconds - now))

        async def add_rate_limit_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(
                    [
                        (b"ratelimit-limit", str(self._request_limit).encode()),
                        (b"ratelimit-remaining", str(remaining).encode()),
                        (b"ratelimit-reset", str(reset_after).encode()),
                    ]
                )
                message["headers"] = headers
            await send(message)

        await self._app(scope, receive, add_rate_limit_headers)

    def _evict_client_if_full(self) -> None:
        if len(self._requests) >= self._max_clients:
            self._requests.popitem(last=False)


def _should_limit(scope: Scope) -> bool:
    return (
        scope["type"] == "http"
        and scope.get("method") != "OPTIONS"
        and scope.get("path") not in {"/health", "/ready"}
    )


def _client_id(scope: Scope) -> str:
    headers = {
        name.lower(): value
        for name, value in scope.get("headers", [])
    }
    for header_name, namespace in (
        (b"x-admin-api-key", "admin"),
        (b"x-api-key", "client"),
    ):
        value = headers.get(header_name)
        if value:
            digest = hashlib.sha256(value).hexdigest()
            return f"{namespace}:{digest}"
    client = scope.get("client")
    host = client[0] if client else "unknown"
    return f"ip:{host}"

import json
import logging
import re
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from time import perf_counter
from uuid import uuid4

from fastapi import Request, Response

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
logger = logging.getLogger("purchase_agent.http")


def get_request_id() -> str | None:
    return _request_id.get()


def _resolve_request_id(request: Request) -> str:
    provided = request.headers.get(REQUEST_ID_HEADER)
    if provided is not None and _REQUEST_ID_PATTERN.fullmatch(provided):
        return provided
    return str(uuid4())


async def request_context_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    request_id = _resolve_request_id(request)
    request.state.request_id = request_id
    token = _request_id.set(request_id)
    started_at = perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        _log_request(request, request_id, 500, started_at, failed=True)
        raise
    else:
        response.headers[REQUEST_ID_HEADER] = request_id
        _log_request(request, request_id, response.status_code, started_at)
        return response
    finally:
        _request_id.reset(token)


def _log_request(
    request: Request,
    request_id: str,
    status_code: int,
    started_at: float,
    *,
    failed: bool = False,
) -> None:
    message = json.dumps(
        {
            "duration_ms": round((perf_counter() - started_at) * 1000, 3),
            "method": request.method,
            "path": request.url.path,
            "request_id": request_id,
            "status_code": status_code,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    if failed:
        logger.exception(message)
    else:
        logger.info(message)

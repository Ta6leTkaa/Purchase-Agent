import secrets
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.core.config import settings

router = APIRouter(include_in_schema=False)
_WEB_ROOT = Path(__file__).resolve().parents[2] / "web"
_SESSION_COOKIE = "purchase_agent_session"


class WebSessionRequest(BaseModel):
    api_key: str = Field(min_length=1, max_length=4096)


@router.post("/app/session", status_code=204)
async def create_web_session(payload: WebSessionRequest, response: Response) -> None:
    expected = settings.api_key
    if expected is not None and not secrets.compare_digest(
        payload.api_key.encode("utf-8"),
        expected.get_secret_value().encode("utf-8"),
    ):
        raise HTTPException(
            status_code=403,
            detail=(
                "Неверный API-ключ. Скопируйте актуальное значение API_KEY "
                f"из .env; сейчас оно содержит "
                f"{len(expected.get_secret_value())} символов."
            ),
        )
    response.set_cookie(
        key=_SESSION_COOKIE,
        value=payload.api_key,
        httponly=True,
        samesite="strict",
        secure=settings.environment == "production",
        path="/",
    )


@router.delete("/app/session", status_code=204)
async def delete_web_session(response: Response) -> None:
    response.delete_cookie(key=_SESSION_COOKIE, path="/")


@router.get("/", response_class=RedirectResponse)
async def web_root() -> RedirectResponse:
    return RedirectResponse(url="/app", status_code=307)


@router.get("/app", response_class=FileResponse)
async def web_app() -> FileResponse:
    return FileResponse(
        _WEB_ROOT / "index.html",
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/app/app.css", response_class=FileResponse)
async def web_styles() -> FileResponse:
    return FileResponse(
        _WEB_ROOT / "app.css",
        media_type="text/css",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/app/app.js", response_class=FileResponse)
async def web_script() -> FileResponse:
    return FileResponse(
        _WEB_ROOT / "app.js",
        media_type="text/javascript",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/demo/cinema", response_class=FileResponse)
async def demo_cinema() -> FileResponse:
    return FileResponse(
        _WEB_ROOT / "demo.html",
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/demo/hotel", response_class=FileResponse)
async def demo_hotel() -> FileResponse:
    return FileResponse(
        _WEB_ROOT / "demo_hotel.html",
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )

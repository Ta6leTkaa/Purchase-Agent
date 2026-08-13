from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, RedirectResponse

router = APIRouter(include_in_schema=False)
_WEB_ROOT = Path(__file__).resolve().parents[2] / "web"


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
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/app/app.js", response_class=FileResponse)
async def web_script() -> FileResponse:
    return FileResponse(
        _WEB_ROOT / "app.js",
        media_type="text/javascript",
        headers={"Cache-Control": "public, max-age=3600"},
    )

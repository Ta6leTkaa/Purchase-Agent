from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.exception_handlers import (
    register_provider_resolution_exception_handlers,
)
from app.api.middleware.request_context import request_context_middleware
from app.api.routes.admin import router as admin_router
from app.api.routes.health import router as health_router
from app.api.routes.identities import router as identities_router
from app.api.routes.missions import router as missions_router
from app.api.routes.providers import router as providers_router
from app.core.config import Settings, settings


def create_app(config: Settings = settings) -> FastAPI:
    application = FastAPI(title=config.app_name)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_allowed_origins,
        allow_credentials=False,
        allow_methods=["DELETE", "GET", "OPTIONS", "PATCH", "POST", "PUT"],
        allow_headers=[
            "Content-Type",
            "Idempotency-Key",
            "If-Match",
            "If-None-Match",
            "X-Admin-API-Key",
            "X-API-Key",
            "X-Request-ID",
        ],
        expose_headers=["ETag", "X-Request-ID"],
    )
    application.middleware("http")(request_context_middleware)
    register_provider_resolution_exception_handlers(application)
    application.include_router(health_router)
    application.include_router(identities_router)
    application.include_router(missions_router)
    application.include_router(providers_router)
    application.include_router(admin_router)
    return application


app = create_app()

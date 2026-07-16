from fastapi import FastAPI

from app.api.routes.admin import router as admin_router
from app.api.routes.health import router as health_router
from app.api.routes.identities import router as identities_router
from app.api.routes.missions import router as missions_router

app = FastAPI(title="Purchase Agent API")
app.include_router(health_router)
app.include_router(identities_router)
app.include_router(missions_router)
app.include_router(admin_router)

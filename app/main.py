from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import init_db
from app.auth import router as auth_router
from app.api.users import router as users_router
from app.api.admin import router as admin_router
from app.api.health import router as health_router
from app.api import health_summary
from app.api import dashboard
from app.api import dashboard_page
from app.api import user_detail
from app.api.provisioning import router as provisioning_router
from app.api.systems import router as systems_router
from app.api.reconciliation import router as reconciliation_router
from app.api.telephony_resources import extensions_router, huntgroups_router, ipo_router

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    force=True,
)

logging.getLogger("app.avaya").setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Avaya IP Office Provisioning API",
    version="3.0.0",
    lifespan=lifespan,
)

app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(users_router, prefix="/api/users", tags=["users"])
app.include_router(extensions_router, prefix="/api/extensions", tags=["extensions"])
app.include_router(huntgroups_router, prefix="/api/huntgroups", tags=["huntgroups"])
app.include_router(ipo_router, prefix="/api/ipo", tags=["ipo-inventory"])
app.include_router(admin_router, prefix="/api/admin", tags=["admin"])
app.include_router(health_router, prefix="/api/health", tags=["health"])
app.include_router(health_summary.router)
app.include_router(dashboard.router)
app.include_router(dashboard_page.router)
app.include_router(user_detail.router)
app.include_router(provisioning_router)
app.include_router(systems_router)
app.include_router(reconciliation_router, prefix="/api/provisioning",)


@app.get("/")
def root():
    return {"service": "avaya-fastapi", "version": "3.0.0", "docs": "/docs"}


@app.get("/health", include_in_schema=False)
def liveness():
    # Container/orchestrator probe: intentionally lightweight and unauthenticated.
    return {"status": "ok"}

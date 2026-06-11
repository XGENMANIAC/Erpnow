"""
FastAPI application entrypoint.

Phase 2: M-Pesa callback webhook wired; DB tables created on startup.
"""
from __future__ import annotations

import sentry_sdk
import structlog
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sentry_sdk.integrations.fastapi import FastApiIntegration

from app.config import settings

logger = structlog.get_logger(__name__)

if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        integrations=[FastApiIntegration()],
        traces_sample_rate=0.1 if settings.is_production else 1.0,
    )

app = FastAPI(
    title="Agentic CRM Integration Service",
    description="ERPNext + M-Pesa + WhatsApp integration layer for the Agentic CRM system",
    version="0.4.0",
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
)

# ── Routers ───────────────────────────────────────────────────────────────────
from app.api.agent import router as agent_router  # noqa: E402
from app.api.mpesa_webhook import router as mpesa_router  # noqa: E402
from app.api.whatsapp_webhook import router as whatsapp_router  # noqa: E402

app.include_router(mpesa_router)
app.include_router(whatsapp_router)
app.include_router(agent_router)


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def on_startup() -> None:
    # Best-effort DB table creation; failure is logged but does not abort startup
    # so the service remains available when the DB is temporarily unreachable.
    try:
        from app.db.models import Base
        from app.db.session import get_engine
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("db_tables_ready")
    except Exception as exc:
        logger.warning("db_init_failed", error=str(exc))

    logger.info(
        "service_starting",
        env=settings.app_env,
        erp_url=settings.erpnext_base_url,
        phase=4,
    )


@app.on_event("shutdown")
async def on_shutdown() -> None:
    logger.info("service_stopping")


# ── Ops endpoints ─────────────────────────────────────────────────────────────

@app.get("/health", tags=["ops"])
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "env": settings.app_env})


@app.get("/info", tags=["ops"])
async def info() -> JSONResponse:
    return JSONResponse(
        {
            "service": "agentic-crm-integration",
            "version": "0.4.0",
            "phase": 4,
            "erp_url": settings.erpnext_base_url,
            "features": {
                "mpesa": bool(settings.mpesa_consumer_key),
                "website_buy": settings.feature_website_buy,
                "extra_channels": settings.feature_extra_channels,
            },
        }
    )

"""
FastAPI application entrypoint.

Phase 1: exposes only /health and /info.
Full REST API routes are wired in Phase 2 via app/api/.
"""
from __future__ import annotations

import sentry_sdk
import structlog
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sentry_sdk.integrations.fastapi import FastApiIntegration

from app.config import settings

logger = structlog.get_logger(__name__)

# ── Sentry (only when DSN is configured) ────────────────────
if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        integrations=[FastApiIntegration()],
        traces_sample_rate=0.1 if settings.is_production else 1.0,
    )

app = FastAPI(
    title="Agentic CRM Integration Service",
    description="ERPNext integration layer for the Agentic CRM system (Phase 1)",
    version="0.1.0",
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
)


@app.get("/health", tags=["ops"])
async def health() -> JSONResponse:
    """Liveness probe — always 200 if the service is running."""
    return JSONResponse({"status": "ok", "env": settings.app_env})


@app.get("/info", tags=["ops"])
async def info() -> JSONResponse:
    """Basic service information (non-sensitive)."""
    return JSONResponse(
        {
            "service": "agentic-crm-integration",
            "version": "0.1.0",
            "phase": 1,
            "erp_url": settings.erpnext_base_url,
            "features": {
                "website_buy": settings.feature_website_buy,
                "extra_channels": settings.feature_extra_channels,
            },
        }
    )


@app.on_event("startup")
async def on_startup() -> None:
    logger.info(
        "service_starting",
        env=settings.app_env,
        erp_url=settings.erpnext_base_url,
    )


@app.on_event("shutdown")
async def on_shutdown() -> None:
    logger.info("service_stopping")

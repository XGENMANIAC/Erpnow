"""
FastAPI dependency injection helpers.

Usage:
    @router.get("/example")
    async def example(erp: ERPNextClient = Depends(get_erp_client)):
        ...
"""
from __future__ import annotations

from typing import AsyncGenerator

from fastapi import Depends

from app.config import settings
from app.erp.client import ERPNextClient


async def get_erp_client() -> AsyncGenerator[ERPNextClient, None]:
    """Yield a per-request ERPNext client that is properly closed after the response."""
    async with ERPNextClient(
        base_url=settings.erpnext_base_url,
        api_key=settings.erpnext_api_key,
        api_secret=settings.erpnext_api_secret,
    ) as client:
        yield client


# Re-export for convenience so callers only need to import from app.deps
__all__ = ["get_erp_client"]

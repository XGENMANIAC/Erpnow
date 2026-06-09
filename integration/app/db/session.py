"""
Async database session factory (stub).

Phase 2 will configure a real asyncpg connection pool.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# Engine is created lazily at startup; stub is safe to import at module level.
_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    """Return (creating if needed) the async SQLAlchemy engine."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(settings.database_url, echo=settings.debug)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return (creating if needed) the async session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _session_factory


async def get_session() -> AsyncSession:
    """FastAPI dependency: yield a DB session, commit on success."""
    factory = get_session_factory()
    async with factory() as session:
        yield session

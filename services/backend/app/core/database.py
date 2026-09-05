"""Async SQLAlchemy engine/session setup.

The Supabase DATABASE_URL in this project points at the Supavisor
*transaction-mode* pooler (port 6543). Transaction-mode pooling hands out a
different physical Postgres backend per transaction, so server-side prepared
statements (which asyncpg uses automatically) can't be safely reused across
calls -- that shows up as intermittent `DuplicatePreparedStatementError`.
We avoid it by disabling asyncpg's statement cache and by not layering our
own connection pool on top of Supavisor's (NullPool).
"""
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


def _asyncpg_url(raw_url: str) -> str:
    """Rewrite a plain postgresql:// URL to use the asyncpg driver."""
    if raw_url.startswith("postgresql+asyncpg://"):
        return raw_url
    if raw_url.startswith("postgresql://"):
        return raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return raw_url


settings = get_settings()

engine = create_async_engine(
    _asyncpg_url(settings.database_url),
    poolclass=NullPool,
    connect_args={"statement_cache_size": 0},
)

async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a request-scoped DB session."""
    async with async_session_factory() as session:
        yield session


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """FastAPI dependency yielding the session *factory*, for work that must
    outlive the request -- specifically the Phase 3 background memory-capture
    task (app/memory/capture.py).

    This exists as its own overridable dependency for exactly one reason:
    without it, the background task would close over the module-level
    `async_session_factory` above, which is bound to whatever DATABASE_URL was
    set at import time -- the *real* one in tests/conftest.py. Tests override
    this dependency the same way they override get_session, so background
    memory writes stay on the in-memory SQLite DB instead of attempting a live
    Postgres connection.
    """
    return async_session_factory

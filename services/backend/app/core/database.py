"""Async SQLAlchemy engine/session setup.

The engine keeps a small pool of open connections. It used to open a new one
per request (NullPool), and from India to the Tokyo pooler a connection costs
~1.2s -- paid on every message, before anything else happened.

How it pools depends on which Supabase pooler DATABASE_URL points at, because
the two differ on the one thing that matters here, prepared statements:

* Session mode (port 5432, recommended). Each of our connections keeps one
  Postgres backend for as long as it stays open, so prepared statements are
  safe to cache and a repeated query is a single round trip.
* Transaction mode (port 6543). Every transaction may land on a different
  backend, so a statement prepared in one is missing -- or a clashing name
  already exists -- in the next. Caching it fails loudly and intermittently
  (Phase 1's DuplicatePreparedStatementError; measured again 2026-09-25: 255
  "prepared statement does not exist" errors out of 480 queries with the
  cache on). So both caches are off and every name is unique.

Measured against the real database, 3 queries per request, 480 queries under
concurrent load with 0 errors in each configuration kept here:
  transaction pooler, NullPool (before)      2.51s
  transaction pooler, pooled, caches off     1.95s
  session pooler, pooled, caches on          1.36s
"""
from collections.abc import AsyncGenerator
from uuid import uuid4

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

#: Supabase's convention: the pooler's transaction mode listens on 6543.
TRANSACTION_POOLER_PORT = 6543


class Base(DeclarativeBase):
    pass


def _asyncpg_url(raw_url: str) -> str:
    """Rewrite a plain postgresql:// URL to use the asyncpg driver."""
    if raw_url.startswith("postgresql+asyncpg://"):
        return raw_url
    if raw_url.startswith("postgresql://"):
        return raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return raw_url


def build_engine(raw_url: str) -> AsyncEngine:
    """The engine for whichever pooler mode raw_url points at."""
    url = make_url(_asyncpg_url(raw_url))
    if url.port == TRANSACTION_POOLER_PORT:
        # The dialect's own statement cache is a URL option, not an engine one.
        url = url.update_query_dict({"prepared_statement_cache_size": "0"})
        return create_async_engine(
            url,
            # Client connections to the pooler are cheap (it allows hundreds),
            # so this pool can be roomy.
            pool_size=5,
            max_overflow=5,
            # No pre-ping: with caches off it costs ~3 round trips per request
            # (measured 1.95s -> 2.43s), which cancels what pooling saves.
            # ponytail: a connection the pooler dropped fails one request and
            # is then discarded; recycling well inside its idle timeout keeps
            # that rare. Switch to session mode to get the ping cheaply.
            pool_recycle=120,
            connect_args={
                "statement_cache_size": 0,
                "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4()}__",
            },
        )
    return create_async_engine(
        url,
        # Each open connection here holds one of the project's backend
        # connections (15 on the free tier), shared with every other process
        # -- local dev, preflight, the deployed backend. Keep it small.
        pool_size=3,
        max_overflow=2,
        # One cached round trip, and it means a connection that went stale
        # while idle is replaced instead of failing someone's message.
        pool_pre_ping=True,
        pool_recycle=300,
        # Serve from the most recently used connections, so the extras sit
        # idle and get recycled instead of being kept warm for nothing.
        pool_use_lifo=True,
    )


settings = get_settings()

engine = build_engine(settings.database_url)

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

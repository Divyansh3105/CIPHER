"""Shared fixtures and fakes for the backend test suite.

Everything the `client` fixture needs -- the fake LLM provider, the fake
embedder, the in-memory vector store, and the SQLite-backed app client
itself -- lives here rather than in a separate tests/fakes.py: `tests/` has no
`__init__.py`, so a same-package import there would depend on pytest's
rootdir sys.path insertion, which is an easy footgun. conftest.py fixtures are
resolved automatically by every test module in this directory.
"""
import math
import os
from datetime import datetime, timezone
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import undefer
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user_id
from app.core.config import get_settings
from app.core.database import Base, get_session, get_session_factory
from app.llm.base import LLMMessage, LLMProvider, LLMResponse
from app.llm.router import LLMRouter, get_llm_router
from app.main import app
from app.memory.capture import get_memory_writer
from app.memory.embedder import Embedder, EmbeddingError, get_embedder
from app.memory.store import MEMORY_DEDUP_SIMILARITY, MemoryHit, MemoryStore, get_memory_store, hash_content
from app.models.db import Memory, User

get_settings.cache_clear()


@pytest.fixture
def dev_user_id():
    return get_settings().dev_user_id


# --- Fake LLM provider -------------------------------------------------


class RecordingProvider(LLMProvider):
    """Fake provider that remembers what it was asked.

    Exposes `.last_messages` so tests can assert on the exact system prompt
    the endpoint built, and `.calls` for anything that needs the full history
    of requests (e.g. distinguishing the chat call from a background
    extraction call in memory-capture tests).
    """

    name = "fake-primary"

    def __init__(self, reply: str = "Acknowledged.") -> None:
        self.reply = reply
        self.calls: list[list[LLMMessage]] = []

    @property
    def last_messages(self) -> list[LLMMessage]:
        return self.calls[-1]

    async def agenerate(self, messages: list[LLMMessage]) -> LLMResponse:
        self.calls.append(messages)
        return LLMResponse(content=self.reply, model="fake-model", provider=self.name)


@pytest.fixture
def provider():
    return RecordingProvider()


# --- Fake embedder -------------------------------------------------------


_EMBEDDER_DIM = 256

# Skipped when building the fake bag-of-words vector below -- without this,
# short common English words dominate the vector and swamp the signal from
# the distinctive words that actually indicate topical similarity (verified
# empirically: unfiltered, an "unrelated" sentence could score *higher*
# cosine similarity than a genuinely related one, purely from shared
# stopwords and small-dimension hash collisions).
_STOPWORDS = {
    "i", "a", "an", "the", "is", "my", "do", "you", "not", "it", "to", "of",
    "in", "on", "for", "and", "she", "he", "they", "we", "was", "were", "be",
    "been", "that", "this", "what", "who", "me", "your", "are", "am",
}


def _bag_of_words_vector(text: str) -> list[float]:
    """Deterministic but *semantically meaningful* embedding: hash each
    lowercased, stopword-filtered word into one of `_EMBEDDER_DIM` buckets
    and sum. Texts sharing distinctive words end up genuinely similar, so
    similarity-threshold behaviour is actually exercised by tests, not just
    plumbing.
    """
    vector = [0.0] * _EMBEDDER_DIM
    for word in text.lower().split():
        word = word.strip(".,!?;:\"'")
        if not word or word in _STOPWORDS:
            continue
        vector[hash(word) % _EMBEDDER_DIM] += 1.0
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0:
        return vector
    return [x / norm for x in vector]


class FakeEmbedder(Embedder):
    name = "fake"
    dim = _EMBEDDER_DIM
    model = "fake-embedding-model"

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str]] = []

    async def aembed(self, texts: list[str], *, task: str) -> list[list[float]]:
        self.calls.append((texts, task))
        return [_bag_of_words_vector(t) for t in texts]


class FailingEmbedder(Embedder):
    name = "failing"
    dim = _EMBEDDER_DIM
    model = "failing-embedding-model"

    async def aembed(self, texts: list[str], *, task: str) -> list[list[float]]:
        raise EmbeddingError("simulated embedding failure")


@pytest.fixture
def embedder():
    return FakeEmbedder()


# --- Fake vector store -----------------------------------------------------


class InMemoryVectorStore(MemoryStore):
    """Real ORM reads/writes, fake similarity math.

    Unlike PgVectorStore (app/memory/store.py), this is safe to use with the
    ORM directly because the SQLite test DB's embedding column is a plain
    TEXT column (see app/models/vector.py) -- there is no asyncpg `vector`
    codec problem to work around here. Everything except the `<=>` operator
    itself is real: real SQL, real user scoping, real expiry filtering, real
    ordering/threshold logic.
    """

    async def search(self, session, *, user_id, embedding, limit, min_similarity=0.0):
        result = await session.execute(
            select(Memory)
            .options(undefer(Memory.embedding))
            .where(Memory.user_id == user_id, Memory.embedding.is_not(None))
        )
        candidates = result.scalars().all()
        now = datetime.now(timezone.utc)

        def _cosine(a: list[float], b: list[float]) -> float:
            dot = sum(x * y for x, y in zip(a, b))
            norm_a = math.sqrt(sum(x * x for x in a))
            norm_b = math.sqrt(sum(x * x for x in b))
            if norm_a == 0 or norm_b == 0:
                return 0.0
            return dot / (norm_a * norm_b)

        hits = []
        for memory in candidates:
            if memory.expires_at is not None:
                expires_at = memory.expires_at
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                if expires_at <= now:
                    continue
            similarity = _cosine(embedding, memory.embedding)
            if similarity >= min_similarity:
                hits.append(
                    MemoryHit(
                        id=memory.id,
                        content=memory.content,
                        memory_type=memory.memory_type,
                        persona=memory.persona,
                        similarity=similarity,
                    )
                )
        hits.sort(key=lambda h: h.similarity, reverse=True)
        return hits[:limit]

    async def add_if_new(
        self,
        session,
        *,
        user_id,
        content,
        embedding,
        memory_type,
        source,
        persona=None,
        conversation_id=None,
    ):
        content_hash = hash_content(content)

        existing = await session.execute(
            select(Memory).where(Memory.user_id == user_id, Memory.content_hash == content_hash)
        )
        existing_memory = existing.scalar_one_or_none()
        if existing_memory is not None:
            return existing_memory, True

        if embedding is not None:
            hits = await self.search(session, user_id=user_id, embedding=embedding, limit=1, min_similarity=0.0)
            if hits and hits[0].similarity >= MEMORY_DEDUP_SIMILARITY:
                duplicate = await session.get(Memory, hits[0].id)
                if duplicate is not None:
                    return duplicate, True

        memory = Memory(
            user_id=user_id,
            conversation_id=conversation_id,
            content=content,
            content_hash=content_hash,
            memory_type=memory_type,
            source=source,
            persona=persona,
            embedding=embedding,
        )
        session.add(memory)
        await session.flush()
        return memory, False

    async def mark_recalled(self, session, memory_ids):
        if not memory_ids:
            return
        result = await session.execute(select(Memory).where(Memory.id.in_(memory_ids)))
        for memory in result.scalars().all():
            memory.last_recalled_at = datetime.now(timezone.utc)

    async def list_for_user(self, session, *, user_id, limit, offset, q=None):
        query = select(Memory).options(undefer(Memory.embedding)).where(Memory.user_id == user_id)
        if q:
            query = query.where(Memory.content.ilike(f"%{q}%"))
        query = query.order_by(Memory.created_at.desc()).limit(limit).offset(offset)
        result = await session.execute(query)
        memories = result.scalars().all()
        return [(m, m.embedding is None) for m in memories]

    async def get_for_user(self, session, *, memory_id, user_id):
        result = await session.execute(
            select(Memory).options(undefer(Memory.embedding)).where(Memory.id == memory_id)
        )
        memory = result.scalar_one_or_none()
        if memory is None or memory.user_id != user_id:
            return None
        return memory, memory.embedding is None

    async def set_embedding(self, session, *, memory_id, embedding):
        memory = await session.get(Memory, memory_id)
        if memory is not None:
            memory.embedding = embedding


@pytest.fixture
def memory_store():
    return InMemoryVectorStore()


# --- Fake memory writer (no-op by default) --------------------------------


class RecordingWriter:
    """Default `get_memory_writer` override: records what it was called with
    but never touches the DB or an LLM. Used by every test that isn't
    specifically exercising memory capture, so background capture can't
    contaminate `provider.last_messages`/`provider.calls` assertions made by
    ordinary chat/persona tests (background tasks run to completion before
    `client.post(...)` returns -- see app/api/chat.py).
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def capture(self, *, session_factory, user_id, conversation_id, persona_id, user_text) -> None:
        self.calls.append(
            {
                "user_id": user_id,
                "conversation_id": conversation_id,
                "persona_id": persona_id,
                "user_text": user_text,
            }
        )


@pytest.fixture
def memory_writer():
    return RecordingWriter()


# --- Raw DB session (for store-level unit tests, no HTTP layer) ------------


@pytest.fixture
async def db_session():
    """A bare in-memory SQLite session with all tables created -- for tests
    that exercise app/memory/store.py directly instead of going through the
    HTTP API (see tests/test_memory_store.py).
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


# --- The app client --------------------------------------------------------


@pytest.fixture
async def client(provider, embedder, memory_store, memory_writer):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    user_id = uuid4()

    async with session_factory() as session:
        session.add(User(id=user_id, email="test@example.com", name="Test User", preferences={}))
        await session.commit()

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_session_factory] = lambda: session_factory
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    app.dependency_overrides[get_llm_router] = lambda: LLMRouter(primary=provider, fallback=provider)
    app.dependency_overrides[get_embedder] = lambda: embedder
    app.dependency_overrides[get_memory_store] = lambda: memory_store
    app.dependency_overrides[get_memory_writer] = lambda: memory_writer

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
    await engine.dispose()

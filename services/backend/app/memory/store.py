"""Vector-similarity storage for long-term memory (Phase 3).

`PgVectorStore` is the only code in this codebase that reads or writes
`Memory.embedding` -- see the NOTE on that column in app/models/db.py and the
module docstring of app/models/vector.py for why: asyncpg has no codec for
the `vector` type, so every statement here binds the embedding as a plain
text parameter and casts it explicitly with `CAST(:embedding AS vector)`
rather than letting the ORM bind a `vector`-typed column directly.
"""
import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import Memory

# Semantic-duplicate threshold: if a new candidate's best existing match is at
# or above this cosine similarity, treat it as the same fact and skip the
# insert rather than storing a near-duplicate. Deliberately high -- a
# false-positive dedup silently loses information, whereas a false negative
# just leaves a near-duplicate in the dashboard the user can delete.
#
# Tuned from scripts/memory_golden_set.py's dedup corpus, which surfaced a
# real limitation worth knowing about: on gemini-embedding-001, real
# paraphrases of the same fact scored as low as 0.929 cosine similarity, but
# genuinely DIFFERENT facts that share a topic and only differ in a specific
# value (a date, an allergen, a day of the week -- e.g. "my flight is on the
# 5th" vs "my flight is on the 15th") scored as high as 0.971. Those ranges
# overlap, so no threshold perfectly separates the two classes. 0.975 is set
# above the highest observed same-topic-different-value score, accepting
# that some genuine paraphrases (the low-0.9x ones) won't dedup -- per the
# asymmetry above, that's the safer side to err on.
MEMORY_DEDUP_SIMILARITY = 0.975


def hash_content(content: str) -> str:
    """Cheap exact-duplicate guard: sha256 of whitespace-normalised, lowercased
    content. Catches literal repeats and retried background writes at zero
    query cost; see MEMORY_DEDUP_SIMILARITY above for the semantic guard this
    doesn't catch.
    """
    normalised = " ".join(content.lower().split())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def _to_vector_literal(embedding: list[float]) -> str:
    """pgvector's text input format, e.g. "[0.1,0.2,0.3]"."""
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


@dataclass(frozen=True)
class MemoryHit:
    id: UUID
    content: str
    memory_type: str
    persona: str | None
    similarity: float


class MemoryStore(ABC):
    @abstractmethod
    async def search(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        embedding: list[float],
        limit: int,
        min_similarity: float = 0.0,
    ) -> list[MemoryHit]:
        """Top-`limit` memories for this user by cosine similarity to
        `embedding`, ordered most-similar first, filtered to
        `similarity >= min_similarity`. Excludes rows with no embedding yet
        and rows past `expires_at`.
        """
        raise NotImplementedError

    @abstractmethod
    async def add_if_new(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        content: str,
        embedding: list[float] | None,
        memory_type: str,
        source: str,
        persona: str | None = None,
        conversation_id: UUID | None = None,
    ) -> tuple[Memory, bool]:
        """Insert a memory unless a duplicate already exists.

        Returns (memory, deduplicated). `embedding=None` stores the row with
        no vector (embedding_pending -- see app/memory/embedder.py's failure
        policy) and skips the semantic-dedup check; the content-hash check
        still applies.
        """
        raise NotImplementedError

    @abstractmethod
    async def mark_recalled(self, session: AsyncSession, memory_ids: list[UUID]) -> None:
        """Bump `last_recalled_at` for memories that were actually injected
        into a prompt (called from app/api/chat.py after a successful search,
        not from dedup checks).
        """
        raise NotImplementedError

    @abstractmethod
    async def list_for_user(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        limit: int,
        offset: int,
        q: str | None = None,
    ) -> list[tuple[Memory, bool]]:
        """Newest-first page of this user's memories, as (memory,
        embedding_pending) pairs. Never fetches the embedding vector itself
        -- see the NOTE on Memory.embedding in app/models/db.py.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_for_user(
        self, session: AsyncSession, *, memory_id: UUID, user_id: UUID
    ) -> tuple[Memory, bool] | None:
        """A single (memory, embedding_pending) pair, or None if it doesn't
        exist or isn't owned by `user_id` (callers should treat both cases as
        404, matching app/api/chat.py's `_get_owned_conversation`).
        """
        raise NotImplementedError

    @abstractmethod
    async def set_embedding(self, session: AsyncSession, *, memory_id: UUID, embedding: list[float] | None) -> None:
        """Overwrite a memory's embedding in place (PATCH /memory/{id} re-embeds
        on content change; `embedding=None` marks it embedding_pending).
        """
        raise NotImplementedError


class PgVectorStore(MemoryStore):
    async def search(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        embedding: list[float],
        limit: int,
        min_similarity: float = 0.0,
    ) -> list[MemoryHit]:
        if not embedding:
            return []
        rows = await session.execute(
            text(
                """
                SELECT id, content, memory_type, persona,
                       1 - (embedding <=> CAST(:embedding AS vector)) AS similarity
                FROM memories
                WHERE user_id = :user_id
                  AND embedding IS NOT NULL
                  AND (expires_at IS NULL OR expires_at > now())
                ORDER BY embedding <=> CAST(:embedding AS vector)
                LIMIT :limit
                """
            ),
            {"embedding": _to_vector_literal(embedding), "user_id": user_id, "limit": limit},
        )
        hits = [
            MemoryHit(id=r.id, content=r.content, memory_type=r.memory_type, persona=r.persona, similarity=r.similarity)
            for r in rows
        ]
        return [h for h in hits if h.similarity >= min_similarity]

    async def add_if_new(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        content: str,
        embedding: list[float] | None,
        memory_type: str,
        source: str,
        persona: str | None = None,
        conversation_id: UUID | None = None,
    ) -> tuple[Memory, bool]:
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
        )
        session.add(memory)
        await session.flush()  # assigns memory.id

        if embedding is not None:
            await session.execute(
                text("UPDATE memories SET embedding = CAST(:embedding AS vector) WHERE id = :id"),
                {"embedding": _to_vector_literal(embedding), "id": memory.id},
            )

        return memory, False

    async def mark_recalled(self, session: AsyncSession, memory_ids: list[UUID]) -> None:
        if not memory_ids:
            return
        await session.execute(
            text("UPDATE memories SET last_recalled_at = now() WHERE id = ANY(:ids)"),
            {"ids": memory_ids},
        )

    async def list_for_user(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        limit: int,
        offset: int,
        q: str | None = None,
    ) -> list[tuple[Memory, bool]]:
        query = select(Memory).where(Memory.user_id == user_id)
        if q:
            query = query.where(Memory.content.ilike(f"%{q}%"))
        query = query.order_by(Memory.created_at.desc()).limit(limit).offset(offset)
        result = await session.execute(query)
        memories = result.scalars().all()
        if not memories:
            return []

        # A second, narrow query: only checks NULL-ness server-side, so
        # asyncpg is never asked to decode a `vector` value.
        pending_rows = await session.execute(
            text("SELECT id FROM memories WHERE id = ANY(:ids) AND embedding IS NULL"),
            {"ids": [m.id for m in memories]},
        )
        pending_ids = {row[0] for row in pending_rows}
        return [(m, m.id in pending_ids) for m in memories]

    async def get_for_user(
        self, session: AsyncSession, *, memory_id: UUID, user_id: UUID
    ) -> tuple[Memory, bool] | None:
        memory = await session.get(Memory, memory_id)
        if memory is None or memory.user_id != user_id:
            return None
        pending = await session.scalar(
            text("SELECT embedding IS NULL FROM memories WHERE id = :id"), {"id": memory_id}
        )
        return memory, bool(pending)

    async def set_embedding(self, session: AsyncSession, *, memory_id: UUID, embedding: list[float] | None) -> None:
        if embedding is None:
            await session.execute(text("UPDATE memories SET embedding = NULL WHERE id = :id"), {"id": memory_id})
        else:
            await session.execute(
                text("UPDATE memories SET embedding = CAST(:embedding AS vector) WHERE id = :id"),
                {"embedding": _to_vector_literal(embedding), "id": memory_id},
            )


@lru_cache
def get_memory_store() -> MemoryStore:
    """Process-wide singleton, matching app.llm.router.get_llm_router and
    app.memory.embedder.get_embedder. PgVectorStore is stateless, so caching
    only avoids re-allocating it per request.
    """
    return PgVectorStore()

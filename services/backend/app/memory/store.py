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


# --- Phase 4: memory graph -------------------------------------------------

# How many of a node's strongest neighbours to keep as links. A KNN graph
# rather than "every pair above a threshold": with a threshold alone a
# tightly-clustered store degenerates into a hairball where every node links
# to every other and the layout carries no information, while a store of
# unrelated facts comes out as disconnected dust. Keeping each node's nearest
# few gives both a connected graph and a readable one.
MEMORY_GRAPH_NEIGHBOURS = 3

# How many standard deviations above a store's OWN mean pairwise similarity a
# link must sit to be drawn.
#
# This being relative rather than a fixed number is a deliberate correction to
# the obvious design, forced by measurement. scripts/memory_graph_calibrate.py
# --corpus, run against real gemini-embedding-001 output over the labelled
# 8-memory corpus, found similarity across every pair spanning only 0.641 to
# 0.809. The one genuinely related pair (two editor preferences) scores 0.809,
# but "I'm allergic to peanuts" and "I'm learning to play the guitar this
# year" score 0.768, and "I prefer tabs over spaces" and "I work best in the
# mornings" score 0.788. An absolute floor of 0.75 draws the true link and six
# false ones; 0.80 draws only the true link, but purely because 0.809 is the
# single highest value in that corpus -- that is fitting a constant to one
# data point, not choosing a threshold.
#
# The cause is well known: embedding models place short first-person English
# sentences into a narrow cone, so absolute cosine similarity between two
# stored facts is a weak signal. What survives that compression is rank --
# which memories are nearest to this one -- which is why the graph is built
# from KNN, and why the floor is expressed against each store's own
# distribution instead of as a number that would need re-tuning per user.
#
# The honest consequence, and the reason the dashboard says "nearest by
# similarity" rather than "related": a drawn link means two memories are among
# each other's closest, not that a person would call them related.
MEMORY_GRAPH_SIGMA = 1.0

# Absolute lower bound underneath the adaptive floor, guarding the degenerate
# case of a store whose memories really are all near-identical. Deliberately
# far below anything observed on real data; it should rarely bind.
MEMORY_GRAPH_MIN_SIMILARITY = 0.30

# Hard cap on nodes. Pairwise similarity is O(n^2) inside Postgres; at 400
# nodes that is 160k distance computations, which is fine, and beyond it both
# the query and the browser layout stop being interactive.
MEMORY_GRAPH_MAX_NODES = 400


@dataclass(frozen=True)
class MemoryEdge:
    source_id: UUID
    target_id: UUID
    similarity: float


@dataclass(frozen=True)
class MemoryGraph:
    #: (memory, embedding_pending) pairs, matching list_for_user's shape.
    nodes: list[tuple["Memory", bool]]
    edges: list[MemoryEdge]
    #: The floor actually applied, after the adaptive calculation. Returned
    #: rather than left for the caller to recompute, so the UI reports the
    #: real number instead of the requested one.
    min_similarity: float


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

    @abstractmethod
    async def graph_for_user(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        limit: int = MEMORY_GRAPH_MAX_NODES,
        neighbours: int = MEMORY_GRAPH_NEIGHBOURS,
        min_similarity: float | None = None,
    ) -> MemoryGraph:
        """Nodes and links for the memory graph (GET /memory/graph).

        Returns the newest `limit` memories as (memory, embedding_pending)
        pairs -- matching `list_for_user` -- plus an undirected KNN edge list
        over exactly those nodes. Edges are deduplicated so a mutual pair
        appears once, and every edge endpoint is guaranteed to be present in
        the node list: the frontend indexes nodes by id, and a dangling edge
        breaks the whole render rather than just omitting a line.

        A memory whose embedding is still pending comes back as a node with
        no edges, deliberately. Dropping it would make the dashboard and the
        graph disagree about how much the assistant remembers; showing it
        unconnected and flagged says the true thing, which is that it is
        stored but not yet searchable.

        `min_similarity=None` (the default) derives the floor from this
        store's own distribution -- see MEMORY_GRAPH_SIGMA for the
        measurement that ruled out a fixed constant. Passing a number
        overrides it absolutely, which is what the dashboard's slider does.

        Like `list_for_user`, the returned Memory objects never carry their
        embedding vector; the similarity math stays in the database.
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

    #: Shared by the edge query and the empty-graph fallback so the two can
    #: never disagree about what the floor is.
    #:
    #: The CASTs are load-bearing, not decoration. `:override` is legitimately
    #: NULL whenever the floor is adaptive, and it appears only inside
    #: `CASE WHEN :override IS NULL ... ELSE :override`, which gives Postgres
    #: nothing to infer a type from -- it answers
    #: `AmbiguousParameterError: could not determine data type of parameter $3`
    #: and the whole endpoint 503s. The SQLite test fake reimplements this
    #: logic in Python, so the entire suite stayed green while the real query
    #: could not run at all; it was scripts/preflight.py and a live request
    #: that caught it.
    _FLOOR_SQL = """GREATEST(
        CAST(:absolute_floor AS double precision),
        CASE WHEN CAST(:override AS double precision) IS NULL
             THEN s.mean + (CAST(:sigma AS double precision) * s.sd)
             ELSE CAST(:override AS double precision)
        END
    )"""

    async def graph_for_user(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        limit: int = MEMORY_GRAPH_MAX_NODES,
        neighbours: int = MEMORY_GRAPH_NEIGHBOURS,
        min_similarity: float | None = None,
    ) -> MemoryGraph:
        # Nodes first, through the ORM, so the embedding column stays
        # deferred and asyncpg is never asked to decode a `vector`.
        node_rows = await session.execute(
            select(Memory)
            .where(Memory.user_id == user_id)
            .order_by(Memory.created_at.desc())
            .limit(limit)
        )
        memories = list(node_rows.scalars().all())
        if not memories:
            return MemoryGraph(nodes=[], edges=[], min_similarity=min_similarity or 0.0)

        node_ids = [m.id for m in memories]
        # A second, narrow query that only checks NULL-ness server-side, so
        # asyncpg is never asked to decode a `vector` (same trick as
        # list_for_user).
        pending_rows = await session.execute(
            text("SELECT id FROM memories WHERE id = ANY(:ids) AND embedding IS NULL"),
            {"ids": node_ids},
        )
        pending_ids = {row[0] for row in pending_rows}
        nodes = [(m, m.id in pending_ids) for m in memories]

        if len(memories) < 2:
            # One node has nothing to link to. Skip the pair query entirely
            # rather than paying for it.
            return MemoryGraph(nodes=nodes, edges=[], min_similarity=min_similarity or 0.0)

        params = {
            "ids": node_ids,
            "neighbours": neighbours,
            "override": min_similarity,
            "sigma": MEMORY_GRAPH_SIGMA,
            "absolute_floor": MEMORY_GRAPH_MIN_SIMILARITY,
        }

        # The pair query is restricted to `nodes` rather than run over the
        # whole table and filtered afterwards: without that, a user past the
        # node cap would pay O(total^2) to render O(limit^2).
        #
        # `stats` deliberately spans ALL pairs, not just the KNN survivors.
        # Averaging the nearest neighbours only would measure the very
        # population being filtered, and the floor would drift upward with
        # every link it removed.
        rows = await session.execute(
            text(
                f"""
                WITH g AS (
                    SELECT id, embedding
                    FROM memories
                    WHERE id = ANY(:ids)
                      AND embedding IS NOT NULL
                      AND (expires_at IS NULL OR expires_at > now())
                ),
                pairs AS (
                    SELECT a.id AS source_id,
                           b.id AS target_id,
                           1 - (a.embedding <=> b.embedding) AS similarity,
                           row_number() OVER (
                               PARTITION BY a.id ORDER BY a.embedding <=> b.embedding
                           ) AS rank
                    FROM g a
                    JOIN g b ON a.id <> b.id
                ),
                stats AS (
                    SELECT avg(similarity) AS mean,
                           COALESCE(stddev_samp(similarity), 0) AS sd
                    FROM pairs
                )
                SELECT p.source_id, p.target_id, p.similarity, {self._FLOOR_SQL} AS floor
                FROM pairs p CROSS JOIN stats s
                WHERE p.rank <= :neighbours AND p.similarity >= {self._FLOOR_SQL}
                """
            ),
            params,
        )

        # KNN is asymmetric -- b can be among a's nearest without a being
        # among b's -- so the same pair can arrive twice. Collapse on an
        # unordered key and keep one edge.
        edges: dict[tuple[UUID, UUID], MemoryEdge] = {}
        floor = min_similarity
        for row in rows:
            floor = float(row.floor)
            key = (
                (row.source_id, row.target_id)
                if str(row.source_id) < str(row.target_id)
                else (row.target_id, row.source_id)
            )
            if key not in edges:
                edges[key] = MemoryEdge(
                    source_id=key[0], target_id=key[1], similarity=float(row.similarity)
                )

        if floor is None:
            # Every pair was filtered out, so no row carried the computed
            # floor back. Ask for it directly rather than reporting a floor
            # that was never applied.
            floor = await self._floor_for(session, params)

        return MemoryGraph(
            nodes=nodes,
            edges=sorted(edges.values(), key=lambda e: e.similarity, reverse=True),
            min_similarity=floor,
        )

    async def _floor_for(self, session: AsyncSession, params: dict) -> float:
        value = await session.scalar(
            text(
                f"""
                WITH g AS (
                    SELECT id, embedding FROM memories
                    WHERE id = ANY(:ids)
                      AND embedding IS NOT NULL
                      AND (expires_at IS NULL OR expires_at > now())
                ),
                pairs AS (
                    SELECT 1 - (a.embedding <=> b.embedding) AS similarity
                    FROM g a JOIN g b ON a.id <> b.id
                ),
                stats AS (
                    SELECT avg(similarity) AS mean,
                           COALESCE(stddev_samp(similarity), 0) AS sd
                    FROM pairs
                )
                SELECT {self._FLOOR_SQL} FROM stats s
                """
            ),
            params,
        )
        return float(value) if value is not None else MEMORY_GRAPH_MIN_SIMILARITY


@lru_cache
def get_memory_store() -> MemoryStore:
    """Process-wide singleton, matching app.llm.router.get_llm_router and
    app.memory.embedder.get_embedder. PgVectorStore is stateless, so caching
    only avoids re-allocating it per request.
    """
    return PgVectorStore()

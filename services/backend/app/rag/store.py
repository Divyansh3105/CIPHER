"""Vector storage and retrieval for document chunks (Phase 5).

Structurally the same shape as app/memory/store.py, and for the same reason:
asyncpg has no codec for pgvector's `vector` type, so every statement here
binds the embedding as text and casts it explicitly. See app/models/vector.py
for the full explanation. This is the only module that reads or writes
`DocumentChunk.embedding`.

Kept separate from MemoryStore rather than generalised into one
"VectorStore". They look alike today and are pulling apart already: memories
dedup on content and expire, chunks belong to a parent document and carry
page numbers; memory retrieval wants the single best few facts, document
retrieval wants passages grouped by source. One abstraction over both would
be a parameter soup within a phase or two.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import Document, DocumentChunk

#: Passages injected into a prompt. Higher than memory's top-5 because a
#: document answer usually needs a couple of consecutive passages to be
#: complete, where a memory is a self-contained fact.
DOCUMENT_TOP_K = 6

#: Below this, a passage is noise that dilutes the prompt rather than
#: grounding it. Lower than memory's 0.65 on purpose: a question is being
#: matched against prose written for another purpose, not against a fact
#: written to be recalled, so genuine matches score lower.
DOCUMENT_MIN_SIMILARITY = 0.55

#: Character budget for retrieved passages in one prompt. Documents are far
#: longer than memories, so without a ceiling six chunks can crowd out the
#: conversation itself.
DOCUMENT_MAX_CHARS = 6000


def _to_vector_literal(embedding: list[float]) -> str:
    """pgvector's text input format, e.g. "[0.1,0.2,0.3]"."""
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


@dataclass(frozen=True)
class ChunkHit:
    id: UUID
    document_id: UUID
    filename: str
    content: str
    chunk_index: int
    page_number: int | None
    similarity: float

    def cite(self) -> str:
        """How this source is named to the model and in the UI."""
        if self.page_number is not None:
            return f"{self.filename}, page {self.page_number}"
        return self.filename


class DocumentStore(ABC):
    @abstractmethod
    async def add_chunks(
        self,
        session: AsyncSession,
        *,
        document_id: UUID,
        user_id: UUID,
        chunks: list[tuple[str, int, int | None]],
        embeddings: list[list[float] | None],
    ) -> int:
        """Insert chunks as (content, chunk_index, page_number) triples.

        `embeddings` is positional and may contain None where embedding
        failed for that chunk -- the passage is still stored, just not
        searchable, which matches the memory store's failure policy.
        """
        raise NotImplementedError

    @abstractmethod
    async def search(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        embedding: list[float],
        limit: int = DOCUMENT_TOP_K,
        min_similarity: float = DOCUMENT_MIN_SIMILARITY,
        document_ids: list[UUID] | None = None,
    ) -> list[ChunkHit]:
        """Most similar chunks for this user, most-similar first.

        `document_ids` narrows the search to specific documents, for "ask
        this file" rather than "ask everything".
        """
        raise NotImplementedError

    @abstractmethod
    async def delete_chunks(self, session: AsyncSession, *, document_id: UUID) -> int:
        """Remove a document's chunks, for re-ingestion."""
        raise NotImplementedError


class PgVectorDocumentStore(DocumentStore):
    async def add_chunks(
        self,
        session: AsyncSession,
        *,
        document_id: UUID,
        user_id: UUID,
        chunks: list[tuple[str, int, int | None]],
        embeddings: list[list[float] | None],
    ) -> int:
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings must be the same length")

        inserted = 0
        for (content, chunk_index, page_number), embedding in zip(chunks, embeddings):
            await session.execute(
                text(
                    """
                    INSERT INTO document_chunks
                        (id, document_id, user_id, content, chunk_index, page_number, embedding)
                    VALUES
                        (gen_random_uuid(), :document_id, :user_id, :content, :chunk_index,
                         :page_number, CAST(:embedding AS vector))
                    """
                ),
                {
                    "document_id": document_id,
                    "user_id": user_id,
                    "content": content,
                    "chunk_index": chunk_index,
                    "page_number": page_number,
                    "embedding": _to_vector_literal(embedding) if embedding is not None else None,
                },
            )
            inserted += 1
        return inserted

    async def search(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        embedding: list[float],
        limit: int = DOCUMENT_TOP_K,
        min_similarity: float = DOCUMENT_MIN_SIMILARITY,
        document_ids: list[UUID] | None = None,
    ) -> list[ChunkHit]:
        if not embedding:
            return []

        # Only "ready" documents are searchable: a half-ingested file would
        # otherwise answer questions from whichever chunks happened to land
        # first, which is worse than not answering.
        sql = """
            SELECT c.id, c.document_id, d.filename, c.content, c.chunk_index, c.page_number,
                   1 - (c.embedding <=> CAST(:embedding AS vector)) AS similarity
            FROM document_chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE c.user_id = :user_id
              AND c.embedding IS NOT NULL
              AND d.status = 'ready'
        """
        params: dict = {
            "embedding": _to_vector_literal(embedding),
            "user_id": user_id,
            "limit": limit,
        }
        if document_ids:
            sql += " AND c.document_id = ANY(:document_ids)"
            params["document_ids"] = document_ids
        sql += " ORDER BY c.embedding <=> CAST(:embedding AS vector) LIMIT :limit"

        rows = await session.execute(text(sql), params)
        hits = [
            ChunkHit(
                id=row.id,
                document_id=row.document_id,
                filename=row.filename,
                content=row.content,
                chunk_index=row.chunk_index,
                page_number=row.page_number,
                similarity=float(row.similarity),
            )
            for row in rows
        ]
        return [h for h in hits if h.similarity >= min_similarity]

    async def delete_chunks(self, session: AsyncSession, *, document_id: UUID) -> int:
        result = await session.execute(
            delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
        )
        return result.rowcount or 0


async def get_owned_document(
    session: AsyncSession, *, document_id: UUID, user_id: UUID
) -> Document | None:
    """A document, or None if it does not exist or belongs to someone else.

    Callers treat both cases as 404, matching app/api/chat.py and
    app/api/memory.py -- do not reveal that another user's document exists.
    """
    document = await session.get(Document, document_id)
    if document is None or document.user_id != user_id:
        return None
    return document


@lru_cache
def get_document_store() -> DocumentStore:
    """Process-wide singleton, matching get_memory_store / get_llm_router."""
    return PgVectorDocumentStore()

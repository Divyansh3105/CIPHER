"""Background ingestion: extract, chunk, embed, store (Phase 5).

Runs from a FastAPI `BackgroundTasks` callback after the upload response has
already been sent, for the same reason memory capture does
(app/memory/capture.py): embedding a document takes seconds to tens of
seconds, and a request that blocks that long reads as a hang.

The same hard consequence applies. If this raises, the exception surfaces
after the HTTP response has started sending and Starlette turns it into a
confusing RuntimeError instead of a 500. `DocumentIngestor.ingest`'s outer
`except Exception` is a correctness requirement, not sloppy error handling --
but unlike memory capture, silence here is not acceptable either: an upload
that never becomes searchable and never says why is the worst outcome. So
every failure path writes `documents.status = 'failed'` with a readable
`documents.error`, and the dashboard shows it.
"""
import logging
from functools import lru_cache
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.memory.embedder import Embedder, EmbeddingError, get_embedder
from app.models.db import Document
from app.rag.chunk import chunk_pages
from app.rag.extract import ExtractionError, Page, extract
from app.rag.store import DocumentStore, get_document_store

logger = logging.getLogger(__name__)

#: Chunks per embedding request. The embedder batches, but a whole book in
#: one call is a single point of failure and a large retry cost.
EMBED_BATCH = 32


class DocumentIngestor:
    def __init__(self, embedder: Embedder, store: DocumentStore) -> None:
        self._embedder = embedder
        self._store = store

    async def ingest(
        self,
        *,
        session_factory: async_sessionmaker,
        document_id: UUID,
        user_id: UUID,
        filename: str,
        data: bytes,
    ) -> None:
        """Extract, chunk, embed and store, updating the document's status.

        Never raises. See the module docstring.
        """
        try:
            await self._run(
                session_factory=session_factory,
                document_id=document_id,
                user_id=user_id,
                filename=filename,
                data=data,
            )
        except ExtractionError as exc:
            await self._fail(session_factory, document_id, str(exc))
        except Exception as exc:  # noqa: BLE001 -- see the module docstring
            logger.exception("Document ingestion failed for %s", document_id)
            await self._fail(
                session_factory,
                document_id,
                f"Ingestion failed unexpectedly ({type(exc).__name__}). The file is stored but not searchable.",
            )

    async def _run(
        self,
        *,
        session_factory: async_sessionmaker,
        document_id: UUID,
        user_id: UUID,
        filename: str,
        data: bytes,
    ) -> None:
        pages: list[Page] = extract(filename, data)
        chunks = chunk_pages(pages)
        if not chunks:
            raise ExtractionError("No usable text was found in that file.")

        # Embed in batches. A batch that fails leaves its chunks stored but
        # unsearchable rather than losing the document -- same policy as
        # app/memory/embedder.py, and the dashboard reports the shortfall by
        # comparing chunk_count against what is actually searchable.
        embeddings: list[list[float] | None] = []
        for start in range(0, len(chunks), EMBED_BATCH):
            batch = chunks[start : start + EMBED_BATCH]
            try:
                vectors = await self._embedder.aembed([c.content for c in batch], task="document")
            except EmbeddingError as exc:
                logger.warning("Embedding batch failed for document %s: %s", document_id, exc)
                vectors = [None] * len(batch)
            embeddings.extend(vectors)

        async with session_factory() as session:
            # Re-ingestion replaces rather than appends: without this, a
            # retried upload doubles every passage and each copy competes
            # with the other for the same retrieval slots.
            await self._store.delete_chunks(session, document_id=document_id)
            await self._store.add_chunks(
                session,
                document_id=document_id,
                user_id=user_id,
                chunks=[(c.content, c.index, c.page_number) for c in chunks],
                embeddings=embeddings,
            )

            document = await session.get(Document, document_id)
            if document is not None:
                document.chunk_count = len(chunks)
                document.page_count = sum(1 for p in pages if p.number is not None) or None
                embedded = sum(1 for e in embeddings if e is not None)
                if embedded == 0:
                    document.status = "failed"
                    document.error = (
                        "The text was read but none of it could be embedded, so it is not searchable. "
                        "This is usually a temporary embedding-quota problem -- delete and re-upload to retry."
                    )
                else:
                    document.status = "ready"
                    document.error = (
                        None
                        if embedded == len(chunks)
                        else f"{len(chunks) - embedded} of {len(chunks)} passages could not be embedded "
                        f"and will not be searchable."
                    )
            await session.commit()

    async def _fail(self, session_factory: async_sessionmaker, document_id: UUID, message: str) -> None:
        try:
            async with session_factory() as session:
                document = await session.get(Document, document_id)
                if document is not None:
                    document.status = "failed"
                    document.error = message
                await session.commit()
        except Exception:  # noqa: BLE001
            # Nothing left to do but log: this already runs after the
            # response was sent, and raising here would produce the
            # RuntimeError the module docstring warns about.
            logger.exception("Could not record ingestion failure for %s", document_id)


@lru_cache
def get_document_ingestor() -> DocumentIngestor:
    """Process-wide singleton, matching get_memory_writer.

    Overridable as a FastAPI dependency so tests can substitute fakes without
    touching a live embedder (see tests/conftest.py).
    """
    return DocumentIngestor(embedder=get_embedder(), store=get_document_store())

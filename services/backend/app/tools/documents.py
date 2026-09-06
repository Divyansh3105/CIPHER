"""Document search as a tool (Phase 5).

Retrieval over the user's uploaded files, wrapped in the same Tool interface
as web search so the planner chooses between them the same way.

Two decisions worth knowing about:

**Passages are grouped by source in the prompt**, not listed by descending
similarity. Six interleaved fragments from three files read as noise; the
same six grouped under "handbook.pdf, page 4" read as evidence, and the
model cites them far more reliably.

**The citation records the page, and the page comes from the chunker**
(app/rag/chunk.py never lets a chunk span pages, precisely so this is always
true). "Source: handbook.pdf, page 4" is actionable; "source: handbook.pdf"
is barely better than nothing on a 200-page file.
"""
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.memory.embedder import Embedder, EmbeddingError
from app.rag.store import DOCUMENT_MAX_CHARS, DocumentStore
from app.tools.base import Tool, ToolError, ToolResult


class DocumentSearchTool(Tool):
    name = "document_search"
    description = (
        "Search the documents the user has uploaded (PDFs, Word files, notes). "
        "Use it whenever the question is about the user's own files, a report, a contract, "
        "a manual, or anything they say they uploaded. "
        "Do NOT use it for general knowledge or current events."
    )
    requires_permission = False

    def __init__(self, embedder: Embedder, store: DocumentStore) -> None:
        self._embedder = embedder
        self._store = store

    async def run(self, query: str, **context) -> ToolResult:
        session: AsyncSession | None = context.get("session")
        user_id: UUID | None = context.get("user_id")
        if session is None or user_id is None:
            raise ToolError("Document search was called without a database session.")

        try:
            vector = (await self._embedder.aembed([query], task="query"))[0]
        except EmbeddingError as exc:
            raise ToolError(f"Could not embed the question to search documents: {exc}") from exc

        hits = await self._store.search(session, user_id=user_id, embedding=vector)

        if not hits:
            return ToolResult(
                context=(
                    f'Nothing in the uploaded documents matched "{query}". '
                    f"Say so plainly; do not answer from general knowledge and present it as being from their files."
                ),
                citations=[],
                summary=f"searched uploaded documents for “{query}” — no matching passages",
            )

        # Group by source, preserving the order sources first appeared, so
        # the strongest match's document leads.
        grouped: dict[str, list] = {}
        for hit in hits:
            grouped.setdefault(hit.cite(), []).append(hit)

        lines = ["Passages from the user's uploaded documents:", ""]
        citations: list[dict] = []
        budget = DOCUMENT_MAX_CHARS

        for source, source_hits in grouped.items():
            if budget <= 0:
                # Out of room: stop rather than emitting a header with no
                # passage under it, which reads as a source that said nothing.
                break
            lines.append(f"--- {source} ---")
            for hit in source_hits:
                if budget <= 0:
                    break
                content = hit.content[:budget]
                budget -= len(content)
                lines.append(content)
                citations.append(
                    {
                        "kind": "document",
                        "document_id": str(hit.document_id),
                        "filename": hit.filename,
                        "page_number": hit.page_number,
                        "content": content,
                        "similarity": hit.similarity,
                    }
                )
            lines.append("")

        lines.append(
            "Cite the source by name when you use one of these, e.g. "
            '"according to handbook.pdf, page 4". If they do not answer the question, say so.'
        )

        # Distinct FILENAMES, not distinct groups. `grouped` is keyed by
        # cite(), which includes the page number, so counting it reported
        # "4 passages from 5 files" for four passages out of one PDF --
        # those groups were pages, not files.
        files = len({c["filename"] for c in citations})
        return ToolResult(
            context="\n".join(lines),
            citations=citations,
            summary=f"searched uploaded documents for “{query}” — {len(citations)} passage"
            + ("s" if len(citations) != 1 else "")
            + f" from {files} file"
            + ("s" if files != 1 else ""),
        )

    def available(self) -> tuple[bool, str]:
        # Whether the user has any documents is a per-request question, so it
        # is answered in the registry (which has the session) rather than
        # here. See ToolRegistry.available_for.
        return True, ""

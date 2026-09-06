"""Document upload and management: POST/GET/DELETE /documents (Phase 5).

Upload is deliberately a two-stage operation. The request extracts nothing
and embeds nothing -- it validates, records the file, and hands the work to a
`BackgroundTasks` callback, returning immediately with `status: "pending"`.
Embedding a 40-page PDF takes tens of seconds, and a request that blocks that
long reads as a hang no matter what the spinner says.

The cost of that choice is that a document exists before it is searchable, so
every list response carries `status` and the dashboard polls. That is the
honest trade: visible waiting beats invisible waiting.
"""
import logging
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_current_user_id
from app.core.database import get_session, get_session_factory
from app.models.db import Document
from app.models.schemas import DocumentOut, DocumentUploadResponse
from app.rag.extract import MAX_UPLOAD_BYTES, SUPPORTED_EXTENSIONS, ExtractionError, extract
from app.rag.ingest import DocumentIngestor, get_document_ingestor
from app.rag.store import DocumentStore, get_document_store, get_owned_document

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("", response_model=list[DocumentOut])
async def list_documents(
    session: AsyncSession = Depends(get_session),
    user_id: UUID = Depends(get_current_user_id),
) -> list[DocumentOut]:
    # `id` is a tiebreaker, not a sort key: two files uploaded inside the
    # same clock tick have identical created_at, and without it their order
    # flips between requests for no reason the user can see.
    result = await session.execute(
        select(Document)
        .where(Document.user_id == user_id)
        .order_by(Document.created_at.desc(), Document.id.desc())
    )
    return [DocumentOut.model_validate(d) for d in result.scalars().all()]


@router.post("", response_model=DocumentUploadResponse, status_code=201)
async def upload_document(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    user_id: UUID = Depends(get_current_user_id),
    ingestor: DocumentIngestor = Depends(get_document_ingestor),
    session_factory: async_sessionmaker = Depends(get_session_factory),
) -> DocumentUploadResponse:
    filename = (file.filename or "").strip() or "untitled"
    if not filename.lower().endswith(SUPPORTED_EXTENSIONS):
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type. Supported: {', '.join(SUPPORTED_EXTENSIONS)}.",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="That file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"That file is {len(data) / 1_048_576:.1f} MB; the limit is "
            f"{MAX_UPLOAD_BYTES // 1_048_576} MB.",
        )

    # Extraction runs here, in the request, even though embedding does not.
    # It is fast, and it is the only way to reject an unreadable file with a
    # 4xx the user can act on -- a password-protected PDF accepted now and
    # failed silently in the background is a much worse experience than a
    # refusal at the moment of upload. It also produces the content hash the
    # duplicate check needs.
    try:
        pages = extract(filename, data)
    except ExtractionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    from app.rag.extract import content_hash

    digest = content_hash(pages)

    existing = await session.execute(
        select(Document).where(Document.user_id == user_id, Document.content_hash == digest)
    )
    duplicate = existing.scalar_one_or_none()
    if duplicate is not None:
        # Same text, already here. Storing it twice would put two copies of
        # every passage into retrieval, competing for the same slots and
        # producing answers that cite the same thing twice.
        return DocumentUploadResponse(
            document=DocumentOut.model_validate(duplicate), deduplicated=True
        )

    document = Document(
        id=uuid4(),
        user_id=user_id,
        filename=filename[:260],
        content_type=file.content_type or "application/octet-stream",
        size_bytes=len(data),
        content_hash=digest,
        status="pending",
        page_count=sum(1 for p in pages if p.number is not None) or None,
    )
    session.add(document)
    await session.commit()
    await session.refresh(document)

    # Runs after the response is sent -- see app/rag/ingest.py's module
    # docstring for why it must never raise and must always record failure.
    background.add_task(
        ingestor.ingest,
        session_factory=session_factory,
        document_id=document.id,
        user_id=user_id,
        filename=filename,
        data=data,
    )

    return DocumentUploadResponse(document=DocumentOut.model_validate(document), deduplicated=False)


@router.delete("/{document_id}")
async def delete_document(
    document_id: UUID,
    session: AsyncSession = Depends(get_session),
    user_id: UUID = Depends(get_current_user_id),
    store: DocumentStore = Depends(get_document_store),
) -> dict[str, int]:
    document = await get_owned_document(session, document_id=document_id, user_id=user_id)
    if document is None:
        # 404 rather than 403 on someone else's document, matching
        # app/api/chat.py and app/api/memory.py: don't reveal existence.
        raise HTTPException(status_code=404, detail="Document not found")

    # Chunks would go with it via ON DELETE CASCADE, but doing it explicitly
    # keeps the SQLite test path (which has no cascade) behaving the same as
    # Postgres, and makes the count reportable.
    removed = await store.delete_chunks(session, document_id=document_id)
    await session.delete(document)
    await session.commit()

    # 200 with a body rather than 204: apps/web/src/lib/api.ts's request<T>()
    # calls response.json() unconditionally, and an empty 204 would throw a
    # raw SyntaxError the frontend's error banner cannot catch.
    return {"deleted": 1, "chunks_removed": removed}

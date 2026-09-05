"""Memory dashboard endpoints: GET/POST/PATCH/DELETE /memory.

No service layer, matching app/api/chat.py -- each endpoint function is the
orchestrator. Deviates from docs/architecture.md Section 13 (which lists only
GET/POST/DELETE) by adding PATCH: Section 6 lists "edit individual memories"
as an explicit user control, and the dashboard (Phase 3 plan) needs it.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_id
from app.core.database import get_session
from app.memory.embedder import Embedder, EmbeddingError, get_embedder
from app.memory.store import MemoryStore, get_memory_store, hash_content
from app.models.db import Memory
from app.models.schemas import MemoryCreate, MemoryCreateResponse, MemoryOut, MemoryUpdate

router = APIRouter(prefix="/memory", tags=["memory"])


def _memory_out(memory: Memory, *, embedding_pending: bool) -> MemoryOut:
    return MemoryOut(
        id=memory.id,
        content=memory.content,
        memory_type=memory.memory_type,
        source=memory.source,
        persona=memory.persona,
        created_at=memory.created_at,
        updated_at=memory.updated_at,
        last_recalled_at=memory.last_recalled_at,
        expires_at=memory.expires_at,
        embedding_pending=embedding_pending,
    )


async def _get_owned_memory_or_404(
    store: MemoryStore, session: AsyncSession, memory_id: UUID, user_id: UUID
) -> tuple[Memory, bool]:
    found = await store.get_for_user(session, memory_id=memory_id, user_id=user_id)
    if found is None:
        # 404, not 403, on another user's memory -- mirrors
        # app/api/chat.py's _get_owned_conversation: don't reveal existence.
        raise HTTPException(status_code=404, detail="Memory not found")
    return found


@router.get("", response_model=list[MemoryOut])
async def list_memories(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    q: str | None = Query(default=None, max_length=200),
    session: AsyncSession = Depends(get_session),
    user_id: UUID = Depends(get_current_user_id),
    store: MemoryStore = Depends(get_memory_store),
) -> list[MemoryOut]:
    rows = await store.list_for_user(session, user_id=user_id, limit=limit, offset=offset, q=q)
    return [_memory_out(memory, embedding_pending=pending) for memory, pending in rows]


@router.post("", response_model=MemoryCreateResponse, status_code=201)
async def create_memory(
    payload: MemoryCreate,
    session: AsyncSession = Depends(get_session),
    user_id: UUID = Depends(get_current_user_id),
    store: MemoryStore = Depends(get_memory_store),
    embedder: Embedder = Depends(get_embedder),
) -> MemoryCreateResponse:
    try:
        embedding = (await embedder.aembed([payload.content], task="document"))[0]
    except EmbeddingError:
        # Losing a memory the user explicitly typed is worse than it being
        # temporarily unsearchable -- store it anyway and let retrieval skip
        # it until it's re-embedded (see app/memory/embedder.py's failure
        # policy table in the Phase 3 plan).
        embedding = None

    memory, deduplicated = await store.add_if_new(
        session,
        user_id=user_id,
        content=payload.content,
        embedding=embedding,
        memory_type=payload.memory_type.value,
        source="explicit",
    )
    if payload.expires_at is not None and not deduplicated:
        memory.expires_at = payload.expires_at
    await session.commit()

    # A dedup match only ever happens against a memory that already has an
    # embedding of its own (content-hash matches don't care either way, and
    # semantic-dedup matches require the existing row to have one) or,
    # rarely, one still pending from its own original creation -- either way
    # this candidate's own `embedding is None` is not the right signal once
    # deduplicated, so ask the store for the existing row's real state.
    if deduplicated:
        _memory, embedding_pending = await store.get_for_user(session, memory_id=memory.id, user_id=user_id)
    else:
        embedding_pending = embedding is None

    return MemoryCreateResponse(
        memory=_memory_out(memory, embedding_pending=embedding_pending), deduplicated=deduplicated
    )


@router.patch("/{memory_id}", response_model=MemoryOut)
async def update_memory(
    memory_id: UUID,
    payload: MemoryUpdate,
    session: AsyncSession = Depends(get_session),
    user_id: UUID = Depends(get_current_user_id),
    store: MemoryStore = Depends(get_memory_store),
    embedder: Embedder = Depends(get_embedder),
) -> MemoryOut:
    memory, _pending = await _get_owned_memory_or_404(store, session, memory_id, user_id)

    memory.content = payload.content
    memory.content_hash = hash_content(payload.content)

    try:
        embedding = (await embedder.aembed([payload.content], task="document"))[0]
    except EmbeddingError:
        embedding = None
    await store.set_embedding(session, memory_id=memory.id, embedding=embedding)

    await session.commit()
    # `updated_at` has an onupdate=func.now() server default, so it's
    # expired after commit -- refresh (async) rather than let a later plain
    # attribute access trigger an unawaited lazy load (see app/api/chat.py's
    # identical refresh of assistant_message for the same reason).
    await session.refresh(memory)
    return _memory_out(memory, embedding_pending=embedding is None)


@router.delete("/all")
async def delete_all_memories(
    confirm: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
    user_id: UUID = Depends(get_current_user_id),
) -> dict[str, int]:
    # Behind an explicit ?confirm=true -- "forget everything" is one mis-click
    # away from irreversible data loss without it.
    if not confirm:
        raise HTTPException(status_code=400, detail="Pass ?confirm=true to forget everything.")

    result = await session.execute(delete(Memory).where(Memory.user_id == user_id))
    await session.commit()
    return {"deleted": result.rowcount or 0}


@router.delete("/{memory_id}")
async def delete_memory(
    memory_id: UUID,
    session: AsyncSession = Depends(get_session),
    user_id: UUID = Depends(get_current_user_id),
    store: MemoryStore = Depends(get_memory_store),
) -> dict[str, int]:
    memory, _pending = await _get_owned_memory_or_404(store, session, memory_id, user_id)
    await session.delete(memory)
    await session.commit()
    # 200 with a body, not 204: apps/web/src/lib/api.ts's request<T>() calls
    # response.json() unconditionally, and an empty 204 body would throw a
    # raw SyntaxError the frontend's error banner can't catch.
    return {"deleted": 1}

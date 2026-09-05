"""Chat endpoints: POST /chat/message, GET /chat/conversations[/{id}]."""
import logging
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user_id
from app.core.database import get_session, get_session_factory
from app.llm.base import LLMMessage, LLMProviderError
from app.llm.router import LLMRouter, get_llm_router
from app.memory.capture import MemoryWriter, get_memory_writer
from app.memory.embedder import Embedder, EmbeddingError, get_embedder
from app.memory.store import MemoryStore, get_memory_store
from app.models.db import Conversation, Message
from app.models.schemas import (
    ChatMessageRequest,
    ChatMessageResponse,
    ConversationDetail,
    ConversationOut,
    MessageOut,
)
from app.personas import PERSONAS, build_system_prompt, get_persona

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

# How many prior messages (user + assistant) to include as context.
HISTORY_LIMIT = 30
TITLE_MAX_LEN = 60

# Memory retrieval knobs (Phase 3). Tuned against real gemini-embedding-001
# output in scripts/memory_golden_set.py (see its similarity matrix + FP/FN
# counts across candidate thresholds): 0.65 had zero false negatives and only
# 1 false positive across an 8-memory x 10-query corpus, versus 2 FPs at 0.62
# and 2 false NEGATIVES (missed real matches) starting at 0.70 -- 0.65 is the
# point where raising the bar further starts costing recall instead of buying
# precision. Re-run that script and adjust here if real usage disagrees.
MEMORY_TOP_K = 5
MEMORY_MIN_SIMILARITY = 0.65
MEMORY_MAX_CHARS = 1200


async def _get_owned_conversation(session: AsyncSession, conversation_id: UUID, user_id: UUID) -> Conversation:
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user_id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


def _derive_title(content: str) -> str:
    title = " ".join(content.split())
    if len(title) > TITLE_MAX_LEN:
        title = title[: TITLE_MAX_LEN - 1].rstrip() + "…"
    return title


def _label_for_history(message: Message, current_persona_id: str) -> str:
    """Prefix a prior turn's content with its persona if it differs from the
    persona now replying, so the model doesn't mistake another persona's
    words for its own past voice after a mid-conversation switch.
    """
    if message.role != "assistant" or message.persona is None or message.persona == current_persona_id:
        return message.content
    label = PERSONAS[get_persona(message.persona).id].display_name
    return f"[{label}] {message.content}"


@router.post("/message", response_model=ChatMessageResponse)
async def send_message(
    payload: ChatMessageRequest,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    user_id: UUID = Depends(get_current_user_id),
    llm_router: LLMRouter = Depends(get_llm_router),
    embedder: Embedder = Depends(get_embedder),
    memory_store: MemoryStore = Depends(get_memory_store),
    memory_writer: MemoryWriter = Depends(get_memory_writer),
    session_factory: async_sessionmaker = Depends(get_session_factory),
) -> ChatMessageResponse:
    if payload.conversation_id is not None:
        conversation = await _get_owned_conversation(session, payload.conversation_id, user_id)
        persona = get_persona(payload.persona or conversation.persona)
    else:
        persona = get_persona(payload.persona)
        conversation = Conversation(user_id=user_id, persona=persona.id, title=_derive_title(payload.content))
        session.add(conversation)
        await session.flush()  # assigns conversation.id

    user_message = Message(
        conversation_id=conversation.id, role="user", content=payload.content, persona=persona.id
    )
    session.add(user_message)
    await session.flush()

    history_result = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.desc())
        .limit(HISTORY_LIMIT)
    )
    history = list(reversed(history_result.scalars().all()))
    mixed_history = any(
        m.role == "assistant" and m.persona is not None and m.persona != persona.id for m in history
    )

    # Memory retrieval (Phase 3). Wrap ONLY the embed call in try/except -- by
    # this point the user Message is already flushed, so swallowing a
    # SQLAlchemyError from the DB search would leave the session in a failed
    # transaction state that explodes confusingly at commit(). Let DB errors
    # bubble to app/main.py's SQLAlchemyError handler instead. Retrieval is
    # NOT filtered by persona -- all three personas share one memory store
    # (docs/architecture.md Section 3); only the framing in the prompt
    # differs (see PersonaConfig.memory_framing).
    recalled_hits = []
    try:
        query_vector = (await embedder.aembed([payload.content], task="query"))[0]
    except EmbeddingError as exc:
        logger.warning("Memory recall skipped, embedding failed: %s", exc)
    else:
        recalled_hits = await memory_store.search(
            session,
            user_id=user_id,
            embedding=query_vector,
            limit=MEMORY_TOP_K,
            min_similarity=MEMORY_MIN_SIMILARITY,
        )

    recalled_contents: list[str] = []
    recalled_snapshot: list[dict] = []
    budget = MEMORY_MAX_CHARS
    for hit in recalled_hits:
        if budget <= 0:
            break
        content = hit.content[:budget]
        recalled_contents.append(content)
        recalled_snapshot.append({"id": str(hit.id), "content": content, "similarity": hit.similarity})
        budget -= len(content)

    system_prompt = build_system_prompt(persona, mixed_history=mixed_history, recalled_memories=recalled_contents)
    llm_messages = [LLMMessage(role="system", content=system_prompt)]
    llm_messages += [
        LLMMessage(role=m.role, content=_label_for_history(m, persona.id)) for m in history
    ]

    try:
        response, fell_back = await llm_router.generate(llm_messages)
    except LLMProviderError as exc:
        await session.rollback()
        raise HTTPException(status_code=502, detail=f"LLM providers unavailable: {exc}") from exc

    reply_content = response.content
    filtered = False
    if persona.output_filter is not None:
        verdict = persona.output_filter(reply_content)
        if not verdict.allowed:
            logger.warning(
                "Output filter tripped: persona=%s rule=%s conversation=%s",
                persona.id,
                verdict.rule,
                conversation.id,
            )
            reply_content = persona.refusal_message
            filtered = True

    assistant_message = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=reply_content,
        persona=persona.id,
        # Denormalised snapshot of what was actually injected into this
        # reply's prompt -- see app/models/db.py's Message.recalled_memories
        # docstring for why this outlives edits/deletes to the memory itself.
        recalled_memories=recalled_snapshot,
    )
    session.add(assistant_message)

    if recalled_hits:
        await memory_store.mark_recalled(session, [hit.id for hit in recalled_hits])

    # Write back the persona that answered (always, even if unchanged) so
    # the conversation reflects a mid-conversation switch, and so this
    # UPDATE fires `updated_at`'s onupdate -- appending a Message alone never
    # touches the conversations row, which otherwise leaves
    # GET /chat/conversations' `ORDER BY updated_at DESC` stuck at creation
    # order (see app/models/db.py).
    conversation.persona = persona.id

    await session.commit()
    await session.refresh(assistant_message)

    # Runs after the response is sent -- see app/memory/capture.py's module
    # docstring for why this must never touch `session` (already closing)
    # and must never raise into the response lifecycle.
    background.add_task(
        memory_writer.capture,
        session_factory=session_factory,
        user_id=user_id,
        conversation_id=conversation.id,
        persona_id=persona.id,
        user_text=payload.content,
    )

    return ChatMessageResponse(
        conversation_id=conversation.id,
        message=MessageOut.model_validate(assistant_message),
        model_used=response.model,
        fell_back=fell_back,
        filtered=filtered,
    )


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(
    session: AsyncSession = Depends(get_session),
    user_id: UUID = Depends(get_current_user_id),
) -> list[ConversationOut]:
    result = await session.execute(
        select(Conversation).where(Conversation.user_id == user_id).order_by(Conversation.updated_at.desc())
    )
    conversations = result.scalars().all()
    return [ConversationOut.model_validate(c) for c in conversations]


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: UUID,
    session: AsyncSession = Depends(get_session),
    user_id: UUID = Depends(get_current_user_id),
) -> ConversationDetail:
    result = await session.execute(
        select(Conversation)
        .options(selectinload(Conversation.messages))
        .where(Conversation.id == conversation_id)
    )
    conversation = result.scalar_one_or_none()
    if conversation is None or conversation.user_id != user_id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return ConversationDetail(
        id=conversation.id,
        persona=conversation.persona,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[MessageOut.model_validate(m) for m in conversation.messages],
    )

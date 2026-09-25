"""Chat endpoints: POST /chat/message[/stream], GET /chat/conversations[/{id}]."""
import json
import logging
from contextlib import aclosing
from dataclasses import dataclass
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user_id
from app.core.database import get_session, get_session_factory
from app.core.ratelimit import CHAT_RULE, RateLimiter, get_rate_limiter
from app.llm.base import LLMMessage, LLMProviderError
from app.llm.registry import ModelUnavailableError
from app.llm.router import LLMRouter, get_llm_router
from app.memory.capture import MemoryWriter, get_memory_writer
from app.memory.embedder import Embedder, EmbeddingError, get_embedder
from app.memory.store import MemoryStore, get_memory_store
from app.models.db import AgentRun, Conversation, Message
from app.models.schemas import (
    ChatMessageRequest,
    ChatMessageResponse,
    ConversationDetail,
    ConversationOut,
    MessageOut,
)
from app.agents.base import AgentContext
from app.agents.orchestrator import Orchestrator
from app.agents.registry import get_orchestrator
from app.personas import PERSONAS, PersonaConfig, build_system_prompt, get_persona
from app.personas.safety import FilterResult, StreamScreen

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

# Phase 6: the orchestrator replaced the Phase 5 tool planner in this
# position. It is not an extra layer -- the routing call that used to choose
# a *tool* now chooses an *agent*, and the research agent chooses the tool.
# Two decisions, two levels, one owner each, and still one routing round trip
# per non-trivial message rather than two.


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


@dataclass
class _Turn:
    """Everything prepared before the model is called, shared by both endpoints."""

    conversation: Conversation
    persona: PersonaConfig
    llm_messages: list[LLMMessage]
    recalled_hits: list
    recalled_snapshot: list[dict]
    citations: list
    agent_used: str | None
    activity: str


async def _prepare_turn(
    payload: ChatMessageRequest,
    *,
    session: AsyncSession,
    user_id: UUID,
    embedder: Embedder,
    memory_store: MemoryStore,
    orchestrator: Orchestrator,
    rate_limiter: RateLimiter,
) -> _Turn:
    # Before any work, including the routing call. A limiter that runs
    # after the expensive part protects nothing.
    allowed, retry_after = rate_limiter.check(f"chat:{user_id}", CHAT_RULE)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Too many messages. Try again in {retry_after}s.",
            headers={"Retry-After": str(retry_after)},
        )

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

    # --- Agent orchestration (Phase 6) --------------------------------
    #
    # Orchestrator.run never raises: every failure path degrades to answering
    # directly, because a broken specialist should cost grounding, not the
    # reply. What must not happen is answering *as if* a specialist had
    # contributed -- so `agent_used` is None when one failed, while `notice`
    # says what was attempted.
    orchestration = await orchestrator.run(
        AgentContext(
            message=payload.content,
            user_id=user_id,
            persona_id=persona.id,
            session=session,
            conversation_id=conversation.id,
            history=[(m.role, m.content) for m in history],
            recalled_memories=recalled_contents,
        )
    )
    tool_context = orchestration.context
    activity = orchestration.notice
    agent_used = orchestration.agent_used
    citations = orchestration.citations

    # Recorded in this request's transaction so a run row and the message it
    # produced commit together -- an audit trail that can disagree with the
    # conversation is worse than none.
    for run in orchestration.runs:
        session.add(run)

    system_prompt = build_system_prompt(
        persona,
        mixed_history=mixed_history,
        recalled_memories=recalled_contents,
        tool_context=tool_context,
        # The persona parameter is still called tool_summary: it labels the
        # retrieved *material*, which is what a tool produced, regardless of
        # which agent asked for it.
        tool_summary=activity,
    )
    llm_messages = [LLMMessage(role="system", content=system_prompt)]
    llm_messages += [
        LLMMessage(role=m.role, content=_label_for_history(m, persona.id)) for m in history
    ]

    return _Turn(
        conversation=conversation,
        persona=persona,
        llm_messages=llm_messages,
        recalled_hits=recalled_hits,
        recalled_snapshot=recalled_snapshot,
        citations=citations,
        agent_used=agent_used,
        activity=activity,
    )


def _log_filter_trip(persona: PersonaConfig, rule: str | None, conversation_id: UUID) -> None:
    logger.warning(
        "Output filter tripped: persona=%s rule=%s conversation=%s", persona.id, rule, conversation_id
    )


async def _finish_turn(
    session: AsyncSession, turn: _Turn, reply_content: str, memory_store: MemoryStore
) -> Message:
    """Persist the reply and everything that commits with it."""
    conversation, persona = turn.conversation, turn.persona
    recalled_snapshot, citations, recalled_hits = turn.recalled_snapshot, turn.citations, turn.recalled_hits

    assistant_message = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=reply_content,
        persona=persona.id,
        # Denormalised snapshot of what was actually injected into this
        # reply's prompt -- see app/models/db.py's Message.recalled_memories
        # docstring for why this outlives edits/deletes to the memory itself.
        recalled_memories=recalled_snapshot,
        # Same reasoning: a citation has to keep saying what the answer was
        # based on, even after the document is deleted or the page changes.
        citations=citations,
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

    return assistant_message


def _capture_later(
    background: BackgroundTasks,
    memory_writer: MemoryWriter,
    session_factory: async_sessionmaker,
    user_id: UUID,
    turn: _Turn,
    user_text: str,
) -> None:
    # Runs after the response is sent -- see app/memory/capture.py's module
    # docstring for why this must never touch `session` (already closing)
    # and must never raise into the response lifecycle.
    background.add_task(
        memory_writer.capture,
        session_factory=session_factory,
        user_id=user_id,
        conversation_id=turn.conversation.id,
        persona_id=turn.persona.id,
        user_text=user_text,
    )


def _response(
    turn: _Turn, message: Message, *, model: str, fell_back: bool, filtered: bool
) -> ChatMessageResponse:
    return ChatMessageResponse(
        conversation_id=turn.conversation.id,
        message=MessageOut.model_validate(message),
        model_used=model,
        fell_back=fell_back,
        filtered=filtered,
        agent_used=turn.agent_used,
        activity=turn.activity,
    )


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
    orchestrator: Orchestrator = Depends(get_orchestrator),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    session_factory: async_sessionmaker = Depends(get_session_factory),
) -> ChatMessageResponse:
    """The whole reply in one response. The web app streams instead
    (/chat/message/stream); this stays for scripts, preflight and anything
    that wants one JSON body."""
    turn = await _prepare_turn(
        payload,
        session=session,
        user_id=user_id,
        embedder=embedder,
        memory_store=memory_store,
        orchestrator=orchestrator,
        rate_limiter=rate_limiter,
    )

    try:
        response, fell_back = await llm_router.generate(turn.llm_messages, user_id=user_id)
    except ModelUnavailableError as exc:
        # Separated from the generic case on purpose: this is a model the
        # user explicitly pinned, and 409 + the pin's own message says which
        # one failed. Answering on a different model instead would defeat
        # the entire point of pinning one (app/llm/router.py).
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LLMProviderError as exc:
        await session.rollback()
        raise HTTPException(status_code=502, detail=f"LLM providers unavailable: {exc}") from exc

    reply_content = response.content
    filtered = False
    if turn.persona.output_filter is not None:
        verdict = turn.persona.output_filter(reply_content)
        if not verdict.allowed:
            _log_filter_trip(turn.persona, verdict.rule, turn.conversation.id)
            reply_content = turn.persona.refusal_message
            filtered = True

    assistant_message = await _finish_turn(session, turn, reply_content, memory_store)
    _capture_later(background, memory_writer, session_factory, user_id, turn, payload.content)
    return _response(turn, assistant_message, model=response.model, fell_back=fell_back, filtered=filtered)


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


@router.post("/message/stream")
async def stream_message(
    payload: ChatMessageRequest,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    user_id: UUID = Depends(get_current_user_id),
    llm_router: LLMRouter = Depends(get_llm_router),
    embedder: Embedder = Depends(get_embedder),
    memory_store: MemoryStore = Depends(get_memory_store),
    memory_writer: MemoryWriter = Depends(get_memory_writer),
    orchestrator: Orchestrator = Depends(get_orchestrator),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    session_factory: async_sessionmaker = Depends(get_session_factory),
) -> StreamingResponse:
    """The reply as server-sent events, so it appears as it is written.

    Everything that can fail with a normal HTTP status -- the rate limit, a
    conversation that is not yours, recall, the agents -- runs before the
    stream opens, so those stay ordinary 4xx/5xx responses. Once streaming,
    each event is one `data:` line of JSON:

        {"type": "delta", "text": "..."}     more of the reply
        {"type": "done", ...}                the saved message; same body as /chat/message
        {"type": "error", "status": 409, "detail": "..."}

    `done` is the only event that means the reply was saved. A stream that
    ends without it -- an error event, a dropped connection -- saved nothing,
    and the client must discard what it showed.

    ULTRON's output filter screens a streamed reply one finished sentence at
    a time (app/personas/safety.py StreamScreen), so nothing reaches the user
    before it is checked. A trip stops generation; `done` then carries the
    refusal, which replaces whatever had already been shown -- exactly what
    the non-streaming endpoint saves and returns.
    """
    turn = await _prepare_turn(
        payload,
        session=session,
        user_id=user_id,
        embedder=embedder,
        memory_store=memory_store,
        orchestrator=orchestrator,
        rate_limiter=rate_limiter,
    )
    persona = turn.persona

    async def events():
        screen = StreamScreen(persona.output_filter) if persona.output_filter else None
        parts: list[str] = []
        model, fell_back, verdict = "", False, FilterResult(True)
        try:
            # aclosing: breaking out on a filter trip must close the provider's
            # stream now, so the model stops generating (and billing) at once.
            async with aclosing(llm_router.stream(turn.llm_messages, user_id=user_id)) as pieces:
                async for piece, fell_back in pieces:
                    model = piece.model
                    parts.append(piece.content)
                    if screen is None:
                        yield _sse({"type": "delta", "text": piece.content})
                        continue
                    ready, verdict = screen.feed(piece.content)
                    if not verdict.allowed:
                        break
                    if ready:
                        yield _sse({"type": "delta", "text": ready})
            if screen is not None and verdict.allowed:
                ready, verdict = screen.flush()
                if verdict.allowed and ready:
                    yield _sse({"type": "delta", "text": ready})
        except ModelUnavailableError as exc:
            await session.rollback()
            yield _sse({"type": "error", "status": 409, "detail": str(exc)})
            return
        except LLMProviderError as exc:
            await session.rollback()
            yield _sse({"type": "error", "status": 502, "detail": f"LLM providers unavailable: {exc}"})
            return

        filtered = not verdict.allowed
        if filtered:
            _log_filter_trip(persona, verdict.rule, turn.conversation.id)
        reply_content = persona.refusal_message if filtered else "".join(parts)

        # ponytail: a database failure here ends the stream without `done`, so
        # the client reports the reply as lost rather than getting a JSON
        # error body. Worth an explicit error event if it is ever seen in logs.
        assistant_message = await _finish_turn(session, turn, reply_content, memory_store)
        _capture_later(background, memory_writer, session_factory, user_id, turn, payload.content)
        done = _response(turn, assistant_message, model=model, fell_back=fell_back, filtered=filtered)
        yield _sse({"type": "done", **done.model_dump(mode="json")})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        # no-transform/X-Accel-Buffering: stop proxies (Render's included)
        # from collecting the whole stream before passing any of it on.
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
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


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: UUID,
    session: AsyncSession = Depends(get_session),
    user_id: UUID = Depends(get_current_user_id),
) -> dict[str, int]:
    """Delete a conversation and its messages.

    Two details that are choices, not incidentals:

    `messages` would go with it through ON DELETE CASCADE, but deleting them
    explicitly keeps the SQLite test path (which does not enforce cascades)
    behaving the same as Postgres, and makes the count reportable. Same
    reasoning as delete_document in app/api/documents.py.

    `agent_runs` are DETACHED rather than deleted, even though their foreign
    key cascades. The activity trail exists to answer "why did it answer that
    way", and Phase 6's rule is that it records every run including the ones
    that did nothing -- an audit view that quietly loses rows because an
    unrelated chat was tidied up is the thing that rule is against. Nulling
    the column first means the cascade has nothing left to take.
    """
    conversation = await _get_owned_conversation(session, conversation_id, user_id)

    detached = await session.execute(
        update(AgentRun)
        .where(AgentRun.conversation_id == conversation.id)
        .values(conversation_id=None)
    )
    removed = await session.execute(
        delete(Message).where(Message.conversation_id == conversation.id)
    )
    await session.delete(conversation)
    await session.commit()

    # 200 with a body rather than 204: apps/web/src/lib/api.ts's request<T>()
    # calls response.json() unconditionally, and an empty 204 would throw a
    # raw SyntaxError the frontend's error banner cannot catch.
    return {
        "deleted": 1,
        "messages_removed": removed.rowcount or 0,
        "agent_runs_detached": detached.rowcount or 0,
    }

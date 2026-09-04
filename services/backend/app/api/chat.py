"""Chat endpoints: POST /chat/message, GET /chat/conversations[/{id}]."""
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user_id
from app.core.database import get_session
from app.llm.base import LLMMessage, LLMProviderError
from app.llm.router import LLMRouter, get_llm_router
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
    session: AsyncSession = Depends(get_session),
    user_id: UUID = Depends(get_current_user_id),
    llm_router: LLMRouter = Depends(get_llm_router),
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

    system_prompt = build_system_prompt(persona, mixed_history=mixed_history)
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
    )
    session.add(assistant_message)

    # Write back the persona that answered (always, even if unchanged) so
    # the conversation reflects a mid-conversation switch, and so this
    # UPDATE fires `updated_at`'s onupdate -- appending a Message alone never
    # touches the conversations row, which otherwise leaves
    # GET /chat/conversations' `ORDER BY updated_at DESC` stuck at creation
    # order (see app/models/db.py).
    conversation.persona = persona.id

    await session.commit()
    await session.refresh(assistant_message)

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

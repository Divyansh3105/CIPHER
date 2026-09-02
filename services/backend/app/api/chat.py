"""Chat endpoints: POST /chat/message, GET /chat/conversations[/{id}]."""
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
from app.personas.jarvis import JARVIS_SYSTEM_PROMPT

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


@router.post("/message", response_model=ChatMessageResponse)
async def send_message(
    payload: ChatMessageRequest,
    session: AsyncSession = Depends(get_session),
    user_id: UUID = Depends(get_current_user_id),
    llm_router: LLMRouter = Depends(get_llm_router),
) -> ChatMessageResponse:
    if payload.conversation_id is not None:
        conversation = await _get_owned_conversation(session, payload.conversation_id, user_id)
    else:
        conversation = Conversation(user_id=user_id, persona="jarvis", title=_derive_title(payload.content))
        session.add(conversation)
        await session.flush()  # assigns conversation.id

    user_message = Message(conversation_id=conversation.id, role="user", content=payload.content, persona="jarvis")
    session.add(user_message)
    await session.flush()

    history_result = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.desc())
        .limit(HISTORY_LIMIT)
    )
    history = list(reversed(history_result.scalars().all()))

    llm_messages = [LLMMessage(role="system", content=JARVIS_SYSTEM_PROMPT)]
    llm_messages += [LLMMessage(role=m.role, content=m.content) for m in history]

    try:
        response, fell_back = await llm_router.generate(llm_messages)
    except LLMProviderError as exc:
        await session.rollback()
        raise HTTPException(status_code=502, detail=f"LLM providers unavailable: {exc}") from exc

    assistant_message = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=response.content,
        persona="jarvis",
    )
    session.add(assistant_message)
    await session.commit()
    await session.refresh(assistant_message)

    return ChatMessageResponse(
        conversation_id=conversation.id,
        message=MessageOut.model_validate(assistant_message),
        model_used=response.model,
        fell_back=fell_back,
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

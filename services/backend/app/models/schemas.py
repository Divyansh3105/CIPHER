"""Pydantic request/response models for the /chat API."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ChatMessageRequest(BaseModel):
    conversation_id: UUID | None = None
    content: str = Field(min_length=1, max_length=8000)


class MessageOut(BaseModel):
    id: UUID
    role: str
    content: str
    persona: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ChatMessageResponse(BaseModel):
    conversation_id: UUID
    message: MessageOut
    model_used: str
    fell_back: bool


class ConversationOut(BaseModel):
    id: UUID
    persona: str
    title: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]

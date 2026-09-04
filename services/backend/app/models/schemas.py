"""Pydantic request/response models for the /chat and /personas APIs."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.personas import Persona


class ChatMessageRequest(BaseModel):
    conversation_id: UUID | None = None
    content: str = Field(min_length=1, max_length=8000)
    # None means "use the conversation's current persona, or the default for
    # a new conversation" -- see app/api/chat.py's persona-resolution step.
    persona: Persona | None = None


class MessageOut(BaseModel):
    id: UUID
    role: str
    content: str
    persona: Persona | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ChatMessageResponse(BaseModel):
    conversation_id: UUID
    message: MessageOut
    model_used: str
    fell_back: bool
    # True when ULTRON's output filter replaced the model's reply with a
    # refusal (app/personas/safety.py). Lets the UI surface that a safety
    # layer actually did something, not just claim to have one.
    filtered: bool = False


class ConversationOut(BaseModel):
    id: UUID
    persona: Persona
    title: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


class PersonaInfo(BaseModel):
    """One entry in GET /personas -- lets the frontend switcher read persona
    labels from the backend instead of hardcoding a second copy of them.
    """

    id: Persona
    display_name: str
    tagline: str

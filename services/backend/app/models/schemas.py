"""Pydantic request/response models for the /chat, /personas, and /memory APIs."""
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from app.personas import Persona


class ChatMessageRequest(BaseModel):
    conversation_id: UUID | None = None
    content: str = Field(min_length=1, max_length=8000)
    # None means "use the conversation's current persona, or the default for
    # a new conversation" -- see app/api/chat.py's persona-resolution step.
    persona: Persona | None = None


class RecalledMemory(BaseModel):
    """One entry in `MessageOut.recalled_memories` -- a snapshot of a memory
    as it was at generation time (see app/models/db.py's Message.recalled_memories
    docstring for why this is a snapshot, not a live reference).
    """

    id: UUID
    content: str
    similarity: float


class MessageOut(BaseModel):
    id: UUID
    role: str
    content: str
    persona: Persona | None
    created_at: datetime
    recalled_memories: list[RecalledMemory] = []

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


# --- Phase 3: memory -----------------------------------------------------


class MemoryType(StrEnum):
    """docs/architecture.md Section 12. Phase 3 only ever *writes*
    LONG_TERM (explicit "remember that..." capture, dashboard entries) and
    SEMANTIC (background LLM extraction) -- SHORT_TERM and EPISODIC are
    reserved for later phases (expiring operational notes, conversation
    summarisation) and are not dead code.
    """

    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"


class MemoryOut(BaseModel):
    id: UUID
    content: str
    memory_type: MemoryType
    source: str
    persona: Persona | None
    created_at: datetime
    updated_at: datetime
    last_recalled_at: datetime | None
    expires_at: datetime | None
    # Computed by the endpoint (embedding IS NULL), not a real column read --
    # see app/models/db.py's NOTE on Memory.embedding for why the ORM never
    # touches that column directly.
    embedding_pending: bool = False

    model_config = {"from_attributes": True}


class MemoryCreate(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    memory_type: MemoryType = MemoryType.LONG_TERM
    expires_at: datetime | None = None


class MemoryUpdate(BaseModel):
    content: str = Field(min_length=1, max_length=2000)


class MemoryCreateResponse(BaseModel):
    memory: MemoryOut
    # True when this content was a near/exact duplicate of an existing memory
    # and no new row was inserted -- see app/memory/store.py's dedup logic.
    # Not an error: POST /memory returns 200 (not 409) when this is true.
    deduplicated: bool = False

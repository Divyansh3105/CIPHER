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


class Citation(BaseModel):
    """One source that grounded a reply (Phase 5).

    A loose shape on purpose: `kind` is "document" or "web" and the other
    fields differ between them. Modelling it as two strict variants would
    mean a schema change every time a tool records one more field, and this
    is a display record, not a contract another system depends on.
    """

    kind: str
    # document
    document_id: UUID | None = None
    filename: str | None = None
    page_number: int | None = None
    content: str | None = None
    similarity: float | None = None
    # web
    title: str | None = None
    url: str | None = None
    snippet: str | None = None
    provider: str | None = None


class MessageOut(BaseModel):
    id: UUID
    role: str
    content: str
    persona: Persona | None
    created_at: datetime
    recalled_memories: list[RecalledMemory] = []
    citations: list[Citation] = []

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
    # Phase 5. `tool_used` is None when no tool ran -- including when one was
    # attempted and failed, in which case `tool_summary` says so. The UI must
    # be able to tell "answered from the model's own knowledge" apart from
    # "tried to look it up and could not", because those deserve different
    # amounts of trust.
    tool_used: str | None = None
    tool_summary: str = ""


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


# --- Phase 5: documents and tools ----------------------------------------


class DocumentStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


class DocumentOut(BaseModel):
    id: UUID
    filename: str
    content_type: str
    size_bytes: int
    status: DocumentStatus
    #: Populated when status is "failed", and readable rather than a stack
    #: trace -- a document that silently never becomes searchable is the
    #: worst outcome, so the reason is always shown.
    error: str | None
    chunk_count: int
    page_count: int | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DocumentUploadResponse(BaseModel):
    document: DocumentOut
    #: True when this file was already uploaded (same extracted text) and no
    #: second copy was made. Not an error: the endpoint returns 200, not 409.
    deduplicated: bool = False


class ToolInfo(BaseModel):
    """One entry in GET /tools."""

    name: str
    description: str
    requires_permission: bool
    #: Whether it can actually run right now, and why not if it cannot. A
    #: tool that is listed but always fails is worse than one that is absent.
    available: bool
    reason: str = ""


# --- Phase 4: runtime model swap -----------------------------------------


class ModelInfo(BaseModel):
    """One entry in GET /models."""

    id: str
    provider: str
    display_name: str
    aliases: list[str]
    note: str = ""


class ActiveModel(BaseModel):
    """What is answering right now.

    `pinned` False means default routing is in effect (primary, with
    automatic fallback). True means the user named this model and it will
    NOT silently fall back -- see app/llm/router.py.
    """

    pinned: bool
    id: str
    provider: str
    display_name: str
    default_id: str


class ModelSwapRequest(BaseModel):
    # Free text on purpose: this is what the user said or typed, not an id.
    # Resolution and refusal happen in app/llm/registry.py.
    spoken: str = Field(min_length=1, max_length=200)


class ModelsResponse(BaseModel):
    active: ActiveModel
    available: list[ModelInfo]


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


class MemoryGraphNode(BaseModel):
    """One memory as a graph node (GET /memory/graph).

    Carries the full content rather than a truncated label: the frontend
    shows the whole memory in an inspector panel when a node is selected, and
    a second round trip per click to fetch text we already had in hand would
    make the graph feel broken.
    """

    id: UUID
    content: str
    memory_type: MemoryType
    source: str
    persona: Persona | None
    created_at: datetime
    last_recalled_at: datetime | None
    # True means stored but not yet searchable -- such a node is always
    # isolated, and the UI says why rather than leaving it a mystery.
    embedding_pending: bool = False


class MemoryGraphLink(BaseModel):
    # Named `source`/`target` rather than `source_id`/`target_id` because
    # that is the shape force-directed graph libraries expect, and renaming
    # in the client would be a pointless second vocabulary.
    source: UUID
    target: UUID
    similarity: float


class MemoryGraphResponse(BaseModel):
    nodes: list[MemoryGraphNode]
    links: list[MemoryGraphLink]
    # Total memories this user has, before the node cap. Reported so the UI
    # can say "showing 400 of 812" instead of quietly drawing a partial
    # picture of what the assistant remembers.
    total: int
    truncated: bool
    # The floor actually applied. When `adaptive` is true this was derived
    # from this store's own similarity distribution rather than requested --
    # a fixed constant does not survive contact with real embeddings, see
    # MEMORY_GRAPH_SIGMA in app/memory/store.py.
    min_similarity: float
    adaptive: bool
    neighbours: int


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

"""SQLAlchemy ORM models.

users, conversations, messages (Phase 1); memories (Phase 3); documents and
document_chunks (Phase 5); agents and agent_runs (Phase 6); tools,
permissions and activity_logs (Phase 7).

Later phases add `tasks` and `notifications` (see docs/architecture.md,
Section 12) -- deliberately not created yet.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.vector import Embedding

# Must match app.memory.embedder.EMBEDDING_DIM.
MEMORY_EMBEDDING_DIM = 768

# Generic types that compile to Postgres-native UUID/JSONB in production but
# also work against SQLite, so the test suite doesn't need a live Postgres
# instance (see tests/conftest.py).
PgUuid = Uuid(as_uuid=True)
PgJson = JSON().with_variant(JSONB(astext_type=Text()), "postgresql")


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(PgUuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    preferences: Mapped[dict] = mapped_column(PgJson, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversations: Mapped[list["Conversation"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(PgUuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(PgUuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    persona: Mapped[str] = mapped_column(String(20), nullable=False, default="jarvis")
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(back_populates="conversation", cascade="all, delete-orphan", order_by="Message.created_at")

    __table_args__ = (
        Index("ix_conversations_user_id", "user_id"),
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(PgUuid, primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PgUuid, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    persona: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Denormalised snapshot of the memories injected into the prompt when
    # this message was generated: [{"id": ..., "content": ..., "similarity":
    # ...}, ...]. Deliberately a snapshot, not a list of memory ids or a join
    # table -- it must keep showing what the model actually saw even after a
    # memory is later edited or deleted (see app/api/memory.py). Only ever
    # populated on assistant messages.
    recalled_memories: Mapped[list] = mapped_column(
        PgJson, nullable=False, default=list, server_default=text("'[]'")
    )

    # Phase 5: what grounded this reply. Either document chunks
    # ({"kind": "document", "document_id", "filename", "page_number",
    # "content", "similarity"}) or web results ({"kind": "web", "title",
    # "url", "snippet"}). A snapshot for the same reason as
    # recalled_memories: a citation must keep saying what the answer was
    # actually based on, even after the document is deleted or the page
    # changes. Only ever populated on assistant messages.
    citations: Mapped[list] = mapped_column(
        PgJson, nullable=False, default=list, server_default=text("'[]'")
    )

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")

    __table_args__ = (
        Index("ix_messages_conversation_id", "conversation_id"),
    )


class ToolRecord(Base):
    """Registry row for an automation action (Phase 7).

    Named ToolRecord rather than Tool because `app.tools.base.Tool` already
    means something different -- a retrieval tool the model may call, with no
    permission model at all. Two things called Tool in one codebase, one of
    which is safety-critical and one of which is not, is a confusion worth
    spending a longer name to avoid.
    """

    __tablename__ = "tools"

    id: Mapped[uuid.UUID] = mapped_column(PgUuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    #: "read_only" | "session" | "sensitive"
    risk: Mapped[str] = mapped_column(String(20), nullable=False)
    requires_permission: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Permission(Base):
    """A grant that lets an action category run (Phase 7).

    Revoked grants are marked, never deleted, so "what was approved, when,
    and when did it stop" stays answerable. The live grant is the newest row
    that is neither revoked nor expired.
    """

    __tablename__ = "permissions"

    id: Mapped[uuid.UUID] = mapped_column(PgUuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(PgUuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    #: An action name or a category. Not a FK -- a grant usually covers a
    #: category, which is not a row in `tools`.
    action_name: Mapped[str] = mapped_column(String(60), nullable=False)
    #: "session" (expires) | "trusted" (until revoked)
    level: Mapped[str] = mapped_column(String(20), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    #: None means "until revoked" -- the trusted allowlist.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_permissions_user_action", "user_id", "action_name"),)


class ActivityLog(Base):
    """One attempted action, whatever became of it (Phase 7).

    Deliberately has NO foreign keys and NO cascade. An audit log that can be
    erased by deleting the thing it describes is not an audit log: rows here
    outlive the action definition, the permission grant, the conversation and
    the user row. Retention is a policy decision, never a side effect of
    another delete.

    `persona` is recorded because docs/architecture.md Section 9 says the
    permission boundary applies to all three personas equally -- so if it
    ever did not, this column is where that would become visible.
    """

    __tablename__ = "activity_logs"

    id: Mapped[uuid.UUID] = mapped_column(PgUuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(PgUuid, nullable=False)
    action_name: Mapped[str] = mapped_column(String(60), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    risk: Mapped[str] = mapped_column(String(20), nullable=False)
    persona: Mapped[str | None] = mapped_column(String(20), nullable=True)
    #: Requested arguments, after redaction (see guard._redact).
    arguments: Mapped[dict] = mapped_column(PgJson, nullable=False, default=dict, server_default=text("'{}'"))
    #: "approved" | "denied" | "executed" | "failed" | "blocked"
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_activity_logs_user_created", "user_id", "created_at"),)


class Agent(Base):
    """Registry row for a specialist agent (Phase 6).

    Deliberately not a definition. What an agent *does* lives in code
    (app/agents/); only what a user may change lives here, which today is
    `enabled` alone. Putting prompts or behaviour in the database would put
    executable intent behind a CRUD endpoint.
    """

    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(PgUuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AgentRun(Base):
    """One agent invocation, recorded whatever happened to it.

    The observability half of Phase 6 and the reason the phase is worth
    anything: "why was that answer ungrounded" is only answerable if the
    skips, timeouts and failures are in the table alongside the successes.

    No foreign key to `agents`: an audit trail must outlive the registry row
    it refers to, so deleting or renaming an agent does not erase the record
    of what it did. `agent_name` is denormalised for the same reason.
    """

    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(PgUuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(PgUuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(PgUuid, nullable=True)
    agent_name: Mapped[str] = mapped_column(String(40), nullable=False)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUuid, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True
    )
    input: Mapped[str] = mapped_column(Text, nullable=False)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: "ok" | "failed" | "timeout" | "skipped"
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Stored rather than derived from timestamps -- created_at is written by
    #: the database clock at insert, which is after the work finished.
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_agent_runs_user_created", "user_id", "created_at"),
        Index("ix_agent_runs_conversation_id", "conversation_id"),
    )


class Document(Base):
    """An uploaded file, chunked and embedded for retrieval (Phase 5).

    `status` is the honest bit of this table. Ingestion happens in the
    background, so a document exists before it is searchable, and the UI has
    to be able to say which. "failed" carries `error` rather than leaving a
    file that silently never answers anything.
    """

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(PgUuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(PgUuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    filename: Mapped[str] = mapped_column(String(260), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    # sha256 of the extracted text, so re-uploading the same file is a no-op
    # rather than a second copy competing with the first in every retrieval.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: "pending" | "ready" | "failed"
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="DocumentChunk.chunk_index"
    )

    __table_args__ = (
        Index("ix_documents_user_id", "user_id"),
        Index("ix_documents_user_content_hash", "user_id", "content_hash", unique=True),
    )


class DocumentChunk(Base):
    """One retrievable passage of a document.

    `user_id` is denormalised from `documents` on purpose: retrieval scopes
    by user on every similarity query, and doing that through a join would
    stop the HNSW index answering the ORDER BY on its own.
    """

    __tablename__ = "document_chunks"

    id: Mapped[uuid.UUID] = mapped_column(PgUuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        PgUuid, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(PgUuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    #: None for formats that have no pages (plain text, Markdown).
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Same NOTE as Memory.embedding below: deferred is load-bearing, not an
    # optimisation. asyncpg has no codec for `vector`.
    embedding: Mapped[list[float] | None] = mapped_column(
        Embedding(MEMORY_EMBEDDING_DIM), nullable=True, deferred=True
    )

    document: Mapped["Document"] = relationship(back_populates="chunks")

    __table_args__ = (
        Index("ix_document_chunks_document_id", "document_id"),
        Index("ix_document_chunks_user_id", "user_id"),
    )


class Memory(Base):
    """A durable fact about a user, retrieved by vector similarity search and
    injected into the system prompt (app/api/chat.py, app/personas/base.py).

    All three personas read from this same store -- see docs/architecture.md
    Section 3 ("Shared memory: All three personas read from the same
    underlying memory store"). `persona` records which persona was active
    when the memory was captured, for display/audit only; it is never used
    to filter retrieval.
    """

    __tablename__ = "memories"

    id: Mapped[uuid.UUID] = mapped_column(PgUuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(PgUuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUuid, ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # sha256 of the whitespace-normalised, lowercased content -- cheap exact-
    # duplicate guard (see app/memory/store.py for the semantic-similarity
    # guard, which catches near-duplicates this hash can't).
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # "short_term" | "long_term" | "episodic" | "semantic" (docs/architecture.md
    # Section 12). Phase 3 only ever writes "long_term" (explicit capture,
    # dashboard entries) and "semantic" (LLM extraction); "short_term" and
    # "episodic" are reserved for later phases (expiring operational notes,
    # conversation summarisation) and are not dead code.
    memory_type: Mapped[str] = mapped_column(String(20), nullable=False, default="long_term")
    # "explicit" (user said "remember that...", or added it via the
    # dashboard) | "extracted" (background LLM extraction pass).
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="explicit")
    persona: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    last_recalled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # NOTE: deferred=True is load-bearing, not an optimisation. asyncpg has no
    # codec for the `vector` type; a plain `select(Memory)` (used by GET
    # /memory, the dashboard, etc.) must never fetch this column on Postgres.
    # All reads/writes of embeddings go through app/memory/store.py using raw
    # SQL with an explicit `::vector` cast. Do NOT add
    # `.options(undefer(Memory.embedding))` anywhere outside a test fake.
    embedding: Mapped[list[float] | None] = mapped_column(
        Embedding(MEMORY_EMBEDDING_DIM), nullable=True, deferred=True
    )

    __table_args__ = (
        Index("ix_memories_user_id", "user_id"),
        Index("ix_memories_user_content_hash", "user_id", "content_hash", unique=True),
    )

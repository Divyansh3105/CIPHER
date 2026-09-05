"""SQLAlchemy ORM models: users, conversations, messages, memories (Phase 3).

Later phases add `documents`/`document_chunks`, `tasks`, `agents`/
`agent_runs`, `tools`, `permissions`, and `activity_logs` (see
docs/architecture.md, Section 12) -- deliberately not created yet.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, Text, Uuid, func, text
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

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")

    __table_args__ = (
        Index("ix_messages_conversation_id", "conversation_id"),
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

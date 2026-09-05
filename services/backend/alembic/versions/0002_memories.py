"""Phase 3 memory tables: memories (pgvector), messages.recalled_memories.

Revision ID: 0002_memories
Revises: 0001_phase1_core
Create Date: 2026-09-05

Notes:
  - The `vector` extension is verified already installed on this Supabase
    project (in the `extensions` schema, which is already on `search_path`),
    so the bare `CREATE EXTENSION IF NOT EXISTS vector` below is a no-op here
    and the bare `vector(768)` type name resolves without qualification. If
    you run this against a fresh Supabase project where that's not the case,
    re-verify with:
        SELECT extnamespace::regnamespace FROM pg_extension WHERE extname = 'vector';
        SHOW search_path;
  - `vector` has no SQLAlchemy core type, so the embedding column is added
    with raw SQL rather than `sa.Column(...)` -- this migration only ever
    runs against Postgres, never SQLite (see app/models/vector.py).
  - Index is HNSW, not IVFFlat. IVFFlat's centroids are trained from whatever
    rows exist at CREATE INDEX time -- building it here against an empty
    table produces a degenerate index that would need dropping and rebuilding
    once real data exists. HNSW builds incrementally and is correct from row
    zero. Defaults (m=16, ef_construction=64) are left as-is.
    NOTE: below roughly 1000 rows, Postgres's planner will usually prefer a
    sequential scan over this index anyway -- that's expected, not a bug; the
    index exists for correctness at scale, not because you'll measure a
    difference in a portfolio-sized dataset.

Run this against MIGRATION_DATABASE_URL (session-mode pooler, port 5432), not
the transaction-mode pooler -- CREATE EXTENSION / CREATE INDEX do not play
well with transaction-mode pooling (see app/core/config.py, app/core/database.py).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0002_memories"
down_revision = "0001_phase1_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "memories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("memory_type", sa.String(length=20), nullable=False, server_default="long_term"),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="explicit"),
        sa.Column("persona", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_recalled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "content_hash", name="ix_memories_user_content_hash"),
    )
    # `vector` has no SQLAlchemy core type -- add it with raw DDL.
    op.execute("ALTER TABLE memories ADD COLUMN embedding vector(768)")
    op.create_index("ix_memories_user_id", "memories", ["user_id"])
    op.execute(
        "CREATE INDEX ix_memories_embedding_hnsw ON memories "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    # Phase 3: snapshot of which memories were injected into the prompt for
    # this reply (see app/models/db.py's Message.recalled_memories docstring
    # for why this is a JSON snapshot, not a memory-id list or join table).
    op.add_column(
        "messages",
        sa.Column(
            "recalled_memories",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("messages", "recalled_memories")
    op.execute("DROP INDEX IF EXISTS ix_memories_embedding_hnsw")
    op.drop_index("ix_memories_user_id", table_name="memories")
    op.drop_table("memories")
    # Deliberately NOT dropping the `vector` extension: Supabase may have
    # installed it independently of this migration, and Phase 5's
    # document_chunks table will need it too. Dropping an extension this
    # migration didn't create is not this migration's business.

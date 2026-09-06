"""Phase 5 RAG tables: documents, document_chunks (pgvector), messages.citations.

Revision ID: 0003_documents
Revises: 0002_memories
Create Date: 2026-09-07

Notes:
  - Same `vector` / HNSW reasoning as 0002_memories: no SQLAlchemy core type
    for `vector`, so the embedding column goes in with raw DDL, and the index
    is HNSW rather than IVFFlat because IVFFlat trains its centroids from
    whatever rows exist at CREATE INDEX time and would be degenerate built
    against an empty table.
  - `documents.status` is a plain string rather than an enum: ingestion is a
    background job with a small, still-settling set of states
    ("pending" -> "ready" | "failed"), and a Postgres enum would need a
    migration every time one is added.
  - `document_chunks` carries `page_number` (nullable) because it is the
    difference between a citation a reader can act on ("page 4 of the
    handbook") and one they cannot ("somewhere in the handbook"). It is
    nullable because plain text and Markdown have no pages.
  - ON DELETE CASCADE from documents to chunks: deleting a document must take
    its chunks with it, or retrieval keeps citing a file the user removed.

Run against MIGRATION_DATABASE_URL (session-mode pooler, port 5432), not the
transaction-mode pooler -- CREATE INDEX does not play well with
transaction-mode pooling (see app/core/database.py).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0003_documents"
down_revision = "0002_memories"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(length=260), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        # sha256 of the extracted text. Re-uploading the same file is a
        # no-op rather than a second copy competing with the first in every
        # future retrieval.
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        # Populated when status becomes "failed" -- a document that silently
        # never becomes searchable is the worst outcome here.
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "content_hash", name="ix_documents_user_content_hash"),
    )
    op.create_index("ix_documents_user_id", "documents", ["user_id"])

    op.create_table(
        "document_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Denormalised from documents so retrieval can scope by user without
        # a join on every similarity query -- the join would defeat the HNSW
        # index's ability to answer the ORDER BY on its own.
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.execute("ALTER TABLE document_chunks ADD COLUMN embedding vector(768)")
    op.create_index("ix_document_chunks_document_id", "document_chunks", ["document_id"])
    op.create_index("ix_document_chunks_user_id", "document_chunks", ["user_id"])
    op.execute(
        "CREATE INDEX ix_document_chunks_embedding_hnsw ON document_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    # Phase 5: snapshot of which document chunks grounded this reply. Same
    # reasoning as messages.recalled_memories in 0002 -- a citation has to
    # keep showing what the model actually saw, even after the document is
    # deleted, so it is a snapshot rather than a chunk-id list.
    op.add_column(
        "messages",
        sa.Column(
            "citations",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("messages", "citations")
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_embedding_hnsw")
    op.drop_index("ix_document_chunks_user_id", table_name="document_chunks")
    op.drop_index("ix_document_chunks_document_id", table_name="document_chunks")
    op.drop_table("document_chunks")
    op.drop_index("ix_documents_user_id", table_name="documents")
    op.drop_table("documents")
    # `vector` extension deliberately left alone -- see 0002_memories.

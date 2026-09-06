"""Phase 6 multi-agent tables: agents, agent_runs.

Revision ID: 0004_agents
Revises: 0003_documents
Create Date: 2026-09-07

Notes:
  - `agents` is a registry row per agent, not a definition: what an agent
    *does* lives in code (app/agents/), and only the things a user can change
    live here -- currently just `enabled`. Storing prompts or behaviour in
    the database would put executable intent behind a CRUD endpoint, which
    is exactly the wrong place for it.
  - `agent_runs` is the observability table and the point of the phase. One
    row per agent invocation with its input, output, status, duration and
    error, so "what did it actually do" is answerable after the fact rather
    than only in logs.
  - `duration_ms` is stored rather than derived from timestamps: the
    interesting number is how long the agent took, and created_at is written
    by the database clock at insert, which is after the work finished.
  - No FK from agent_runs.agent_id to agents.id. Runs are an audit trail and
    must outlive the registry row -- deleting or renaming an agent should not
    erase the record of what it did. `agent_name` is denormalised for the
    same reason.

Run against MIGRATION_DATABASE_URL (session-mode pooler, port 5432).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0004_agents"
down_revision = "0003_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=40), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "agent_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Deliberately no FK to agents.id -- an audit trail must outlive the
        # registry row it refers to.
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_name", sa.String(length=40), nullable=False),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("input", sa.Text(), nullable=False),
        sa.Column("output", sa.Text(), nullable=True),
        #: "ok" | "failed" | "timeout" | "skipped"
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    # Section 12's indexing note: the audit query is always "this user's
    # runs, newest first".
    op.create_index("ix_agent_runs_user_created", "agent_runs", ["user_id", "created_at"])
    op.create_index("ix_agent_runs_conversation_id", "agent_runs", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_runs_conversation_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_user_created", table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_table("agents")

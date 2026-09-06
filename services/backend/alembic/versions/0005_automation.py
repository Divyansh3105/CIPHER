"""Phase 7 automation tables: tools, permissions, activity_logs.

Revision ID: 0005_automation
Revises: 0004_agents
Create Date: 2026-09-07

This is the safety-critical schema (docs/architecture.md Sections 9 and 14).
Three notes on choices that are load-bearing rather than cosmetic:

  - `activity_logs` has NO foreign key to `tools` and NO cascade from
    anything. An audit log that can be erased by deleting the thing it
    describes is not an audit log. Rows here outlive the action definition,
    the permission grant, and the conversation.

  - `permissions.expires_at` is nullable, and null means "until revoked"
    (the trusted allowlist). Session grants always carry an expiry. Storing
    both in one table keeps a single place to ask "may this run", rather
    than two sources that can disagree.

  - `permissions` is scoped by (user_id, action_name) with no unique
    constraint. A revoked grant is not deleted, it is superseded, so the
    history of what was granted and when stays readable. The current grant
    is the newest non-revoked row.

Run against MIGRATION_DATABASE_URL (session-mode pooler, port 5432).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0005_automation"
down_revision = "0004_agents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tools",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=60), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        #: "read_only" | "session" | "sensitive"
        sa.Column("risk", sa.String(length=20), nullable=False),
        sa.Column("requires_permission", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        #: The action or category this grant covers. Not a FK: a grant may
        #: name a category that spans several actions.
        sa.Column("action_name", sa.String(length=60), nullable=False),
        #: "session" (expires) | "trusted" (until revoked)
        sa.Column("level", sa.String(length=20), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        #: NULL means "until revoked". Session grants always set this.
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_permissions_user_action", "permissions", ["user_id", "action_name"])

    op.create_table(
        "activity_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # No cascade, deliberately: deleting a user must not silently erase
        # the record of what was done on their behalf. Retention is a policy
        # decision, not a side effect of another delete.
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_name", sa.String(length=60), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("risk", sa.String(length=20), nullable=False),
        #: The persona active at the time. Recorded because Section 9 says
        #: the boundary applies to all three equally -- so if it ever did
        #: not, the log is where that would be visible.
        sa.Column("persona", sa.String(length=20), nullable=True),
        #: JSON of what was requested, after redaction.
        sa.Column("arguments", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        #: "approved" | "denied" | "executed" | "failed" | "blocked"
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_activity_logs_user_created", "activity_logs", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_activity_logs_user_created", table_name="activity_logs")
    op.drop_table("activity_logs")
    op.drop_index("ix_permissions_user_action", table_name="permissions")
    op.drop_table("permissions")
    op.drop_table("tools")

"""agent planning sessions

Revision ID: 0005_agent_planning_sessions
Revises: 0004_product_ux_asset_metadata
Create Date: 2026-05-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_agent_planning_sessions"
down_revision: str | None = "0004_product_ux_asset_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if not _has_table("agent_planning_sessions"):
        op.create_table(
            "agent_planning_sessions",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=False),
            sa.Column("title", sa.String(length=160), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("current_plan", sa.Text(), nullable=True),
            sa.Column("current_run_payload", sa.Text(), nullable=True),
            sa.Column("rejection_reason", sa.Text(), nullable=True),
            sa.Column("executed_run_id", sa.String(length=36), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
    if not _has_table("agent_planning_messages"):
        op.create_table(
            "agent_planning_messages",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("session_id", sa.String(length=36), nullable=False),
            sa.Column("role", sa.String(length=20), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("plan_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
    op.create_index(
        "ix_agent_planning_sessions_user_id",
        "agent_planning_sessions",
        ["user_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_agent_planning_messages_session_id",
        "agent_planning_messages",
        ["session_id"],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    if _has_table("agent_planning_messages"):
        op.drop_index(
            "ix_agent_planning_messages_session_id",
            table_name="agent_planning_messages",
            if_exists=True,
        )
        op.drop_table("agent_planning_messages")
    if _has_table("agent_planning_sessions"):
        op.drop_index(
            "ix_agent_planning_sessions_user_id",
            table_name="agent_planning_sessions",
            if_exists=True,
        )
        op.drop_table("agent_planning_sessions")

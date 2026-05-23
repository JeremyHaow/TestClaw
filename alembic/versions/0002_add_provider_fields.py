"""add provider fields

Revision ID: 0002_add_provider_fields
Revises: 0001_initial
Create Date: 2026-04-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_add_provider_fields"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    if not _has_column("llm_providers", "system_prompt"):
        op.add_column("llm_providers", sa.Column("system_prompt", sa.String(5000), nullable=True))
    if not _has_column("llm_providers", "agent_type"):
        op.add_column("llm_providers", sa.Column("agent_type", sa.String(50), nullable=True))


def downgrade() -> None:
    if _has_column("llm_providers", "agent_type"):
        op.drop_column("llm_providers", "agent_type")
    if _has_column("llm_providers", "system_prompt"):
        op.drop_column("llm_providers", "system_prompt")

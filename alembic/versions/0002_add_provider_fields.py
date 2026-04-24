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


def upgrade() -> None:
    op.add_column("llm_providers", sa.Column("system_prompt", sa.String(5000), nullable=True))
    op.add_column("llm_providers", sa.Column("agent_type", sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column("llm_providers", "agent_type")
    op.drop_column("llm_providers", "system_prompt")

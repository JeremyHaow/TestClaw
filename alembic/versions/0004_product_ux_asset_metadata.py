"""product ux asset metadata

Revision ID: 0004_product_ux_asset_metadata
Revises: 0003_add_task_enum_values
Create Date: 2026-05-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_product_ux_asset_metadata"
down_revision: str | None = "0003_add_task_enum_values"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("api_documents", sa.Column("source_url", sa.String(length=1000), nullable=True))


def downgrade() -> None:
    op.drop_column("api_documents", "source_url")

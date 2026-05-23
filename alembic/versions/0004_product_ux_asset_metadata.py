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


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    if not _has_column("api_documents", "source_url"):
        op.add_column("api_documents", sa.Column("source_url", sa.String(length=1000), nullable=True))


def downgrade() -> None:
    if _has_column("api_documents", "source_url"):
        op.drop_column("api_documents", "source_url")

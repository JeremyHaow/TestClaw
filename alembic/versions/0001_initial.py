"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    from app.database import Base
    import app.models  # noqa: F401

    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    from app.database import Base
    import app.models  # noqa: F401

    Base.metadata.drop_all(bind=bind)

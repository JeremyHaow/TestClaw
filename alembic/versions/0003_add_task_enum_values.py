"""add task enum values

Revision ID: 0003_add_task_enum_values
Revises: 0002_add_provider_fields
Create Date: 2026-04-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_add_task_enum_values"
down_revision: str | None = "0002_add_provider_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _add_postgres_enum_value(enum_name: str, value: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_type WHERE typname = '{enum_name}'
            ) AND NOT EXISTS (
                SELECT 1
                FROM pg_enum e
                JOIN pg_type t ON e.enumtypid = t.oid
                WHERE t.typname = '{enum_name}' AND e.enumlabel = '{value}'
            ) THEN
                ALTER TYPE {enum_name} ADD VALUE '{value}';
            END IF;
        END;
        $$;
        """
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    _add_postgres_enum_value("testtype", "AUTO")
    _add_postgres_enum_value("testtype", "SUITE")
    _add_postgres_enum_value("taskstatus", "CANCELLED")


def downgrade() -> None:
    # PostgreSQL cannot safely remove enum values in-place while existing rows may use them.
    pass

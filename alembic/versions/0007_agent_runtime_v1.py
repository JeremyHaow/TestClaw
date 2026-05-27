"""agent runtime v1 tables

Revision ID: 0007_agent_runtime_v1
Revises: 0006_run_operational_tables
Create Date: 2026-05-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_agent_runtime_v1"
down_revision: str | None = "0006_run_operational_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if not _has_table("run_agent_actions"):
        op.create_table(
            "run_agent_actions",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("run_id", sa.String(length=36), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("action_id", sa.String(length=160), nullable=False),
            sa.Column("action_type", sa.String(length=80), nullable=True),
            sa.Column("tool_name", sa.String(length=160), nullable=False),
            sa.Column("stage", sa.String(length=80), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("risk", sa.String(length=80), nullable=True),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("inputs_json", sa.JSON(), nullable=False),
            sa.Column("expected_observation", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    if not _has_table("run_agent_observations"):
        op.create_table(
            "run_agent_observations",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("run_id", sa.String(length=36), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("observation_id", sa.String(length=160), nullable=False),
            sa.Column("action_id", sa.String(length=160), nullable=True),
            sa.Column("tool_call_id", sa.String(length=160), nullable=True),
            sa.Column("stage", sa.String(length=80), nullable=False),
            sa.Column("layer", sa.String(length=80), nullable=False),
            sa.Column("tool_name", sa.String(length=160), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("outcome", sa.String(length=64), nullable=False),
            sa.Column("failure_type", sa.String(length=120), nullable=True),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("inputs_json", sa.JSON(), nullable=False),
            sa.Column("outputs_json", sa.JSON(), nullable=False),
            sa.Column("evidence_ids_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    if not _has_table("run_agent_evaluations"):
        op.create_table(
            "run_agent_evaluations",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("run_id", sa.String(length=36), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("evaluation_id", sa.String(length=160), nullable=False),
            sa.Column("stage", sa.String(length=80), nullable=False),
            sa.Column("sufficient_evidence", sa.Boolean(), nullable=False),
            sa.Column("outcome", sa.String(length=64), nullable=False),
            sa.Column("next_action", sa.String(length=80), nullable=False),
            sa.Column("confidence", sa.String(length=32), nullable=False),
            sa.Column("failure_type", sa.String(length=120), nullable=True),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("missing_evidence_json", sa.JSON(), nullable=False),
            sa.Column("replan_hint", sa.Text(), nullable=True),
            sa.Column("observation_ids_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    op.create_index("ix_run_agent_actions_run_id", "run_agent_actions", ["run_id"], unique=False, if_not_exists=True)
    op.create_index("idx_run_agent_actions_run_seq", "run_agent_actions", ["run_id", "sequence"], unique=False, if_not_exists=True)
    op.create_index("idx_run_agent_actions_run_action", "run_agent_actions", ["run_id", "action_id"], unique=False, if_not_exists=True)
    op.create_index("ix_run_agent_observations_run_id", "run_agent_observations", ["run_id"], unique=False, if_not_exists=True)
    op.create_index("idx_run_agent_observations_run_seq", "run_agent_observations", ["run_id", "sequence"], unique=False, if_not_exists=True)
    op.create_index("idx_run_agent_observations_run_action", "run_agent_observations", ["run_id", "action_id"], unique=False, if_not_exists=True)
    op.create_index("idx_run_agent_observations_run_failure", "run_agent_observations", ["run_id", "failure_type"], unique=False, if_not_exists=True)
    op.create_index("idx_run_agent_observations_run_created", "run_agent_observations", ["run_id", "created_at"], unique=False, if_not_exists=True)
    op.create_index("ix_run_agent_evaluations_run_id", "run_agent_evaluations", ["run_id"], unique=False, if_not_exists=True)
    op.create_index("idx_run_agent_evaluations_run_seq", "run_agent_evaluations", ["run_id", "sequence"], unique=False, if_not_exists=True)
    op.create_index("idx_run_agent_evaluations_run_failure", "run_agent_evaluations", ["run_id", "failure_type"], unique=False, if_not_exists=True)
    op.create_index("idx_run_agent_evaluations_run_created", "run_agent_evaluations", ["run_id", "created_at"], unique=False, if_not_exists=True)


def downgrade() -> None:
    if _has_table("run_agent_evaluations"):
        op.drop_index("idx_run_agent_evaluations_run_created", table_name="run_agent_evaluations", if_exists=True)
        op.drop_index("idx_run_agent_evaluations_run_failure", table_name="run_agent_evaluations", if_exists=True)
        op.drop_index("idx_run_agent_evaluations_run_seq", table_name="run_agent_evaluations", if_exists=True)
        op.drop_index("ix_run_agent_evaluations_run_id", table_name="run_agent_evaluations", if_exists=True)
        op.drop_table("run_agent_evaluations")
    if _has_table("run_agent_observations"):
        op.drop_index("idx_run_agent_observations_run_created", table_name="run_agent_observations", if_exists=True)
        op.drop_index("idx_run_agent_observations_run_failure", table_name="run_agent_observations", if_exists=True)
        op.drop_index("idx_run_agent_observations_run_action", table_name="run_agent_observations", if_exists=True)
        op.drop_index("idx_run_agent_observations_run_seq", table_name="run_agent_observations", if_exists=True)
        op.drop_index("ix_run_agent_observations_run_id", table_name="run_agent_observations", if_exists=True)
        op.drop_table("run_agent_observations")
    if _has_table("run_agent_actions"):
        op.drop_index("idx_run_agent_actions_run_action", table_name="run_agent_actions", if_exists=True)
        op.drop_index("idx_run_agent_actions_run_seq", table_name="run_agent_actions", if_exists=True)
        op.drop_index("ix_run_agent_actions_run_id", table_name="run_agent_actions", if_exists=True)
        op.drop_table("run_agent_actions")

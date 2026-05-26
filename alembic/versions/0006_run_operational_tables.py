"""run operational tables

Revision ID: 0006_run_operational_tables
Revises: 0005_agent_planning_sessions
Create Date: 2026-05-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_run_operational_tables"
down_revision: str | None = "0005_agent_planning_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if not _has_table("agent_plans"):
        op.create_table(
            "agent_plans",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("session_id", sa.String(length=36), nullable=True),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("objective", sa.Text(), nullable=False),
            sa.Column("target_json", sa.JSON(), nullable=False),
            sa.Column("scope_json", sa.JSON(), nullable=False),
            sa.Column("auth_json", sa.JSON(), nullable=False),
            sa.Column("safety_json", sa.JSON(), nullable=False),
            sa.Column("success_json", sa.JSON(), nullable=False),
            sa.Column("api_plan_json", sa.JSON(), nullable=True),
            sa.Column("ui_plan_json", sa.JSON(), nullable=True),
            sa.Column("recommended_run_payload_json", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    if not _has_table("run_events"):
        op.create_table(
            "run_events",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("run_id", sa.String(length=36), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("event_type", sa.String(length=128), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=True),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("payload_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    if not _has_table("run_interventions"):
        op.create_table(
            "run_interventions",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("run_id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=True),
            sa.Column("supplemental_instructions", sa.Text(), nullable=False),
            sa.Column("scope", sa.String(length=64), nullable=False),
            sa.Column("cancel_current", sa.Boolean(), nullable=False),
            sa.Column("replan", sa.Boolean(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("applied_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    if not _has_table("run_tool_calls"):
        op.create_table(
            "run_tool_calls",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("run_id", sa.String(length=36), nullable=False),
            sa.Column("node_name", sa.String(length=128), nullable=False),
            sa.Column("tool_name", sa.String(length=128), nullable=False),
            sa.Column("input_summary", sa.Text(), nullable=True),
            sa.Column("input_json", sa.JSON(), nullable=True),
            sa.Column("output_summary", sa.Text(), nullable=True),
            sa.Column("output_json", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("duration_ms", sa.Integer(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    if not _has_table("run_evidence"):
        op.create_table(
            "run_evidence",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("run_id", sa.String(length=36), nullable=False),
            sa.Column("case_id", sa.String(length=36), nullable=True),
            sa.Column("evidence_type", sa.String(length=64), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=True),
            sa.Column("file_path", sa.Text(), nullable=True),
            sa.Column("url", sa.Text(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    if not _has_table("run_findings"):
        op.create_table(
            "run_findings",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("run_id", sa.String(length=36), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("severity", sa.String(length=32), nullable=False),
            sa.Column("confidence", sa.String(length=32), nullable=False),
            sa.Column("category", sa.String(length=64), nullable=False),
            sa.Column("surface", sa.String(length=255), nullable=True),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("evidence_ids_json", sa.JSON(), nullable=False),
            sa.Column("reproduction_steps_json", sa.JSON(), nullable=False),
            sa.Column("next_action", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    if not _has_table("target_memories"):
        op.create_table(
            "target_memories",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("target_key", sa.String(length=512), nullable=False),
            sa.Column("target_label", sa.String(length=255), nullable=False),
            sa.Column("target_type", sa.String(length=64), nullable=False),
            sa.Column("run_count", sa.Integer(), nullable=False),
            sa.Column("last_run_id", sa.String(length=36), nullable=True),
            sa.Column("recurring_themes_json", sa.JSON(), nullable=False),
            sa.Column("known_blockers_json", sa.JSON(), nullable=False),
            sa.Column("reusable_assets_json", sa.JSON(), nullable=False),
            sa.Column("suggested_strategy", sa.Text(), nullable=True),
            sa.Column("confidence", sa.String(length=32), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    if not _has_table("artifacts"):
        op.create_table(
            "artifacts",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("run_id", sa.String(length=36), nullable=True),
            sa.Column("artifact_type", sa.String(length=64), nullable=False),
            sa.Column("storage_backend", sa.String(length=64), nullable=False),
            sa.Column("file_path", sa.Text(), nullable=False),
            sa.Column("public_url", sa.Text(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    op.create_index(
        "ix_agent_plans_session_id",
        "agent_plans",
        ["session_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_run_events_run_id",
        "run_events",
        ["run_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "idx_run_events_run_seq",
        "run_events",
        ["run_id", "sequence"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_run_interventions_run_id",
        "run_interventions",
        ["run_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_run_tool_calls_run_id",
        "run_tool_calls",
        ["run_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_run_evidence_run_id",
        "run_evidence",
        ["run_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_run_findings_run_id",
        "run_findings",
        ["run_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_target_memories_target_key",
        "target_memories",
        ["target_key"],
        unique=True,
        if_not_exists=True,
    )
    op.create_index(
        "ix_artifacts_run_id",
        "artifacts",
        ["run_id"],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    if _has_table("artifacts"):
        op.drop_index("ix_artifacts_run_id", table_name="artifacts", if_exists=True)
        op.drop_table("artifacts")
    if _has_table("target_memories"):
        op.drop_index(
            "ix_target_memories_target_key",
            table_name="target_memories",
            if_exists=True,
        )
        op.drop_table("target_memories")
    if _has_table("run_findings"):
        op.drop_index("ix_run_findings_run_id", table_name="run_findings", if_exists=True)
        op.drop_table("run_findings")
    if _has_table("run_evidence"):
        op.drop_index("ix_run_evidence_run_id", table_name="run_evidence", if_exists=True)
        op.drop_table("run_evidence")
    if _has_table("run_tool_calls"):
        op.drop_index("ix_run_tool_calls_run_id", table_name="run_tool_calls", if_exists=True)
        op.drop_table("run_tool_calls")
    if _has_table("run_interventions"):
        op.drop_index(
            "ix_run_interventions_run_id",
            table_name="run_interventions",
            if_exists=True,
        )
        op.drop_table("run_interventions")
    if _has_table("run_events"):
        op.drop_index("idx_run_events_run_seq", table_name="run_events", if_exists=True)
        op.drop_index("ix_run_events_run_id", table_name="run_events", if_exists=True)
        op.drop_table("run_events")
    if _has_table("agent_plans"):
        op.drop_index("ix_agent_plans_session_id", table_name="agent_plans", if_exists=True)
        op.drop_table("agent_plans")

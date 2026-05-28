import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RunAgentAction(Base):
    __tablename__ = "run_agent_actions"
    __table_args__ = (
        Index("idx_run_agent_actions_run_seq", "run_id", "sequence"),
        Index("idx_run_agent_actions_run_action", "run_id", "action_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    action_id: Mapped[str] = mapped_column(String(160))
    action_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(160))
    stage: Mapped[str] = mapped_column(String(80), default="agent_runtime")
    status: Mapped[str] = mapped_column(String(32), default="pending")
    risk: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    inputs_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    expected_observation: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class RunAgentObservation(Base):
    __tablename__ = "run_agent_observations"
    __table_args__ = (
        Index("idx_run_agent_observations_run_seq", "run_id", "sequence"),
        Index("idx_run_agent_observations_run_action", "run_id", "action_id"),
        Index("idx_run_agent_observations_run_failure", "run_id", "failure_type"),
        Index("idx_run_agent_observations_run_created", "run_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    observation_id: Mapped[str] = mapped_column(String(160))
    action_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    stage: Mapped[str] = mapped_column(String(80))
    layer: Mapped[str] = mapped_column(String(80), default="unknown")
    tool_name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(32))
    outcome: Mapped[str] = mapped_column(String(64))
    failure_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    inputs_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    outputs_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    evidence_ids_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RunAgentEvaluation(Base):
    __tablename__ = "run_agent_evaluations"
    __table_args__ = (
        Index("idx_run_agent_evaluations_run_seq", "run_id", "sequence"),
        Index("idx_run_agent_evaluations_run_failure", "run_id", "failure_type"),
        Index("idx_run_agent_evaluations_run_created", "run_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    evaluation_id: Mapped[str] = mapped_column(String(160))
    stage: Mapped[str] = mapped_column(String(80))
    sufficient_evidence: Mapped[bool] = mapped_column(default=False)
    outcome: Mapped[str] = mapped_column(String(64))
    next_action: Mapped[str] = mapped_column(String(80))
    confidence: Mapped[str] = mapped_column(String(32), default="unknown")
    failure_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    missing_evidence_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    replan_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    observation_ids_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

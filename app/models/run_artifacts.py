import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RunIntervention(Base):
    __tablename__ = "run_interventions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    supplemental_instructions: Mapped[str] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(String(64), default="future_steps")
    cancel_current: Mapped[bool] = mapped_column(Boolean, default=False)
    replan: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(32), default="submitted")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class RunToolCall(Base):
    __tablename__ = "run_tool_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    node_name: Mapped[str] = mapped_column(String(128))
    tool_name: Mapped[str] = mapped_column(String(128))
    input_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    output_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RunEvidence(Base):
    __tablename__ = "run_evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    case_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    evidence_type: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(255))
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RunFinding(Base):
    __tablename__ = "run_findings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    title: Mapped[str] = mapped_column(String(255))
    severity: Mapped[str] = mapped_column(String(32), default="medium")
    confidence: Mapped[str] = mapped_column(String(32), default="medium")
    category: Mapped[str] = mapped_column(String(64))
    surface: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str] = mapped_column(Text)
    evidence_ids_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    reproduction_steps_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    next_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TargetMemory(Base):
    __tablename__ = "target_memories"
    __table_args__ = (Index("ix_target_memories_target_key", "target_key", unique=True),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    target_key: Mapped[str] = mapped_column(String(512))
    target_label: Mapped[str] = mapped_column(String(255))
    target_type: Mapped[str] = mapped_column(String(64))
    run_count: Mapped[int] = mapped_column(Integer, default=0)
    last_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    recurring_themes_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    known_blockers_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    reusable_assets_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    suggested_strategy: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[str] = mapped_column(String(32), default="medium")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    artifact_type: Mapped[str] = mapped_column(String(64))
    storage_backend: Mapped[str] = mapped_column(String(64), default="local")
    file_path: Mapped[str] = mapped_column(Text)
    public_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

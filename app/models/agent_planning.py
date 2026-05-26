import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AgentPlan(Base):
    __tablename__ = "agent_plans"
    __table_args__ = (Index("ix_agent_plans_session_id", "session_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    title: Mapped[str] = mapped_column(String(255), default="Untitled plan")
    objective: Mapped[str] = mapped_column(Text, default="")
    target_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    scope_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    auth_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    safety_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    success_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    api_plan_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    ui_plan_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    recommended_run_payload_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="ready")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class AgentPlanningSession(Base):
    __tablename__ = "agent_planning_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    title: Mapped[str] = mapped_column(String(160), default="New plan")
    status: Mapped[str] = mapped_column(String(40), default="collecting")
    current_plan: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_run_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    executed_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class AgentPlanningMessage(Base):
    __tablename__ = "agent_planning_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    plan_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

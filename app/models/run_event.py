import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, JSON, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RunEvent(Base):
    __tablename__ = "run_events"
    __table_args__ = (Index("idx_run_events_run_seq", "run_id", "sequence"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(128))
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

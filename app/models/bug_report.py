import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class BugReport(Base):
    __tablename__ = "bug_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_id: Mapped[str] = mapped_column(String(36), index=True)
    title: Mapped[str] = mapped_column(String(255))
    root_cause: Mapped[str] = mapped_column(Text)
    reproduce_steps: Mapped[str] = mapped_column(Text)
    error_logs: Mapped[str | None] = mapped_column(Text, nullable=True)
    fix_suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

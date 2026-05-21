import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum as SAEnum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BUG_FOUND = "bug_found"
    CANCELLED = "cancelled"


class TestType(str, enum.Enum):
    AUTO = "AUTO"
    UI = "UI"
    API = "API"
    FUNCTIONAL = "FUNCTIONAL"
    FULL = "FULL"
    SUITE = "SUITE"


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    objective: Mapped[str] = mapped_column(String(500))
    target_url: Mapped[str] = mapped_column(String(500))
    status: Mapped[TaskStatus] = mapped_column(SAEnum(TaskStatus), default=TaskStatus.PENDING)
    test_type: Mapped[TestType] = mapped_column(SAEnum(TestType), default=TestType.FULL)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    generated_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_log: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_doc_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    environment_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TestRun(Base):
    __tablename__ = "test_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    suite_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    env_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="pending")
    allure_report_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    coverage_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

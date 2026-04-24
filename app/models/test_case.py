import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Priority(str, enum.Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class TestCase(Base):
    __tablename__ = "test_cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title: Mapped[str] = mapped_column(String(255))
    preconditions: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    steps: Mapped[list[dict] | list[str]] = mapped_column(JSON)
    expected: Mapped[list[str] | str | None] = mapped_column(JSON, nullable=True)
    test_data: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    priority: Mapped[str] = mapped_column(String(10), default=Priority.P2.value)
    category: Mapped[str] = mapped_column(String(50), default="FUNCTIONAL")
    source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TestSuite(Base):
    __tablename__ = "test_suites"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255))
    test_case_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

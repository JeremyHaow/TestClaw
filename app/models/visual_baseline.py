import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class VisualBaseline(Base):
    __tablename__ = "visual_baselines"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    page_url: Mapped[str] = mapped_column(String(500))
    baseline_path: Mapped[str] = mapped_column(String(500))
    viewport: Mapped[str] = mapped_column(String(100), default="1280x720")
    diff_threshold: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

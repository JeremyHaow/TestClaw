import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Environment(Base):
    __tablename__ = "environments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(100), unique=True)
    base_url: Mapped[str] = mapped_column(String(500))
    variables_encrypted: Mapped[dict] = mapped_column(JSON, default=dict)
    is_production: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

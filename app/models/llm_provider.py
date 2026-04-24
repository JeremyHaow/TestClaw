import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ProviderType(str, enum.Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class LLMProvider(Base):
    __tablename__ = "llm_providers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(100))
    type: Mapped[ProviderType] = mapped_column(SAEnum(ProviderType))
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    api_key_encrypted: Mapped[str] = mapped_column(String(500))
    model_name: Mapped[str] = mapped_column(String(100))
    is_default_coder: Mapped[bool] = mapped_column(Boolean, default=False)
    is_default_vision: Mapped[bool] = mapped_column(Boolean, default=False)
    is_default_planner: Mapped[bool] = mapped_column(Boolean, default=False)
    max_tokens: Mapped[int] = mapped_column(Integer, default=4096)
    temperature: Mapped[float] = mapped_column(Float, default=0.2)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    system_prompt: Mapped[str | None] = mapped_column(String(5000), nullable=True)
    agent_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

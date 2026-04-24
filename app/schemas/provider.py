from pydantic import BaseModel

from app.schemas.common import ORMModel


class ProviderCreate(BaseModel):
    name: str
    type: str
    api_key: str
    model_name: str
    base_url: str | None = None
    is_default_coder: bool = False
    is_default_vision: bool = False
    is_default_planner: bool = False
    max_tokens: int = 4096
    temperature: float = 0.2
    system_prompt: str | None = None
    agent_type: str | None = None


class ProviderRead(ORMModel):
    id: str
    name: str
    type: str
    base_url: str | None = None
    model_name: str
    is_default_coder: bool
    is_default_vision: bool
    is_default_planner: bool
    max_tokens: int
    temperature: float
    is_active: bool
    api_key_masked: str | None = None
    system_prompt: str | None = None
    agent_type: str | None = None

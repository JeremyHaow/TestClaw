from datetime import datetime

from pydantic import BaseModel

from app.schemas.common import ORMModel


class EnvironmentCreate(BaseModel):
    name: str
    base_url: str
    variables: dict[str, str] = {}
    is_production: bool = False


class EnvironmentRead(ORMModel):
    id: str
    name: str
    base_url: str
    variables: dict[str, str]
    is_production: bool
    created_at: datetime

from datetime import datetime

from pydantic import BaseModel

from app.schemas.common import ORMModel


class DocumentCreate(BaseModel):
    name: str
    raw_content: str = ""
    format: str = "openapi"
    url: str | None = None


class DocumentUpdate(BaseModel):
    name: str | None = None
    raw_content: str | None = None
    format: str | None = None
    source_url: str | None = None


class DocumentRead(ORMModel):
    id: str
    name: str
    source_url: str | None = None
    raw_content: str
    format: str
    parsed_endpoints: list[dict]
    created_at: datetime

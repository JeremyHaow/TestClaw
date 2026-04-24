from pydantic import BaseModel

from app.schemas.common import ORMModel


class DocumentCreate(BaseModel):
    name: str
    raw_content: str = ""
    format: str = "openapi"
    url: str | None = None


class DocumentUpdate(BaseModel):
    name: str


class DocumentRead(ORMModel):
    id: str
    name: str
    format: str
    parsed_endpoints: list[dict]

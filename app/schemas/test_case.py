from datetime import datetime

from pydantic import BaseModel

from app.schemas.common import ORMModel


class TestCaseCreate(BaseModel):
    title: str
    steps: list[dict] | list[str]
    expected: list[str] | str | None = None
    priority: str = "P2"
    category: str = "FUNCTIONAL"
    test_data: dict | list | None = None
    source: str | None = None


class TestCaseRead(ORMModel):
    id: str
    title: str
    preconditions: list[str] | None = None
    steps: list[dict] | list[str]
    expected: list[str] | str | None = None
    test_data: dict | list | None = None
    priority: str
    category: str
    source: str | None = None
    created_at: datetime

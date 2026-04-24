from datetime import datetime

from app.schemas.common import ORMModel


class RunRead(ORMModel):
    id: str
    suite_id: str | None = None
    env_id: str | None = None
    task_id: str | None = None
    status: str
    allure_report_path: str | None = None
    coverage_pct: int | None = None
    created_at: datetime

from datetime import datetime

from app.schemas.common import ORMModel


class BugReportRead(ORMModel):
    id: str
    task_id: str
    title: str
    root_cause: str
    reproduce_steps: str
    error_logs: str | None = None
    fix_suggestion: str | None = None
    created_at: datetime

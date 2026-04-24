from pydantic import BaseModel


class DashboardSummary(BaseModel):
    total_tasks: int
    total_documents: int
    total_providers: int
    total_environments: int
    recent_tasks: list[dict]
    tasks_by_status: dict[str, int] = {}

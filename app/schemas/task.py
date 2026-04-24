import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.common import ORMModel


class TaskCreate(BaseModel):
    objective: str
    target_url: str
    test_type: Literal["ui", "api", "functional", "full"] = "full"
    api_doc_id: str | None = None
    environment_id: str | None = None


class WorkflowStep(BaseModel):
    node: str
    status: str
    detail: str | None = None


class TaskRead(ORMModel):
    id: str
    objective: str
    target_url: str
    status: str
    test_type: str
    retry_count: int
    generated_code: str | None = None
    execution_log: str | None = None
    api_doc_id: str | None = None
    environment_id: str | None = None
    created_at: datetime


class TaskDetailRead(TaskRead):
    workflow_steps: list[WorkflowStep] = []
    test_plan: list[dict] | None = None
    test_cases: list[dict] | None = None
    execution_result: dict | None = None
    bug_report: dict | None = None


def parse_task_detail(task_orm) -> dict:
    log_str = getattr(task_orm, "execution_log", None) or ""
    parsed = {}
    try:
        parsed = json.loads(log_str) if log_str else {}
    except Exception:
        parsed = {}

    base = {
        "id": task_orm.id,
        "objective": task_orm.objective,
        "target_url": task_orm.target_url,
        "status": task_orm.status.value if hasattr(task_orm.status, "value") else str(task_orm.status),
        "test_type": task_orm.test_type.value if hasattr(task_orm.test_type, "value") else str(task_orm.test_type),
        "retry_count": task_orm.retry_count,
        "generated_code": task_orm.generated_code,
        "execution_log": task_orm.execution_log,
        "api_doc_id": task_orm.api_doc_id,
        "environment_id": task_orm.environment_id,
        "created_at": task_orm.created_at.isoformat() if task_orm.created_at else None,
        "workflow_steps": parsed.get("workflow_steps", []),
        "test_plan": parsed.get("test_plan"),
        "test_cases": parsed.get("test_cases"),
        "execution_result": parsed.get("execution_result"),
        "bug_report": parsed.get("bug_report"),
    }
    return base

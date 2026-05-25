import json
from datetime import datetime

from pydantic import BaseModel, field_validator

from app.core.redaction import redact_json_text, redact_sensitive_data
from app.schemas.common import ORMModel
from app.services.task_service import normalize_test_type


class TaskCreate(BaseModel):
    objective: str
    target_url: str
    test_type: str = "full"
    api_doc_id: str | None = None
    environment_id: str | None = None

    @field_validator("test_type")
    @classmethod
    def validate_test_type(cls, value: str) -> str:
        return normalize_test_type(value).value.lower()


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

    @field_validator("execution_log")
    @classmethod
    def redact_execution_log(cls, value: str | None) -> str | None:
        return redact_json_text(value) if value else value


class TaskListItemRead(ORMModel):
    id: str
    target_url: str
    objective: str
    status: str
    test_type: str
    created_at: datetime
    updated_at: datetime | None = None
    error_message: str | None = None


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
    parsed = redact_sensitive_data(parsed)
    safe_execution_log = redact_json_text(log_str) if log_str else task_orm.execution_log

    base = {
        "id": task_orm.id,
        "objective": task_orm.objective,
        "target_url": task_orm.target_url,
        "status": task_orm.status.value if hasattr(task_orm.status, "value") else str(task_orm.status),
        "test_type": task_orm.test_type.value if hasattr(task_orm.test_type, "value") else str(task_orm.test_type),
        "retry_count": task_orm.retry_count,
        "generated_code": task_orm.generated_code,
        "execution_log": safe_execution_log,
        "api_doc_id": task_orm.api_doc_id,
        "environment_id": task_orm.environment_id,
        "created_at": task_orm.created_at.isoformat() if task_orm.created_at else None,
        "workflow_steps": parsed.get("workflow_steps", []),
        "current_step": parsed.get("current_step"),
        "progress_events": parsed.get("progress_events", []),
        "test_plan": parsed.get("test_plan"),
        "test_cases": parsed.get("test_cases"),
        "execution_result": parsed.get("execution_result"),
        "bug_report": parsed.get("bug_report"),
        "api_plan": parsed.get("api_plan"),
        "ui_plan": parsed.get("ui_plan"),
        "api_cases": parsed.get("api_cases"),
        "ui_cases": parsed.get("ui_cases"),
        "api_execution_result": parsed.get("api_execution_result"),
        "ui_execution_result": parsed.get("ui_execution_result"),
        "final_report": parsed.get("final_report"),
        "artifacts": parsed.get("artifacts"),
        "tool_registry": parsed.get("tool_registry"),
        "skill_plan": parsed.get("skill_plan"),
        "tool_calls": parsed.get("tool_calls"),
        "tool_summary": parsed.get("tool_summary"),
        "evidence_evaluation": parsed.get("evidence_evaluation"),
        "agent_evaluations": parsed.get("agent_evaluations"),
        "agent_attempt_history": parsed.get("agent_attempt_history"),
        "agent_replan_counts": parsed.get("agent_replan_counts"),
        "agent_replan_feedback": parsed.get("agent_replan_feedback"),
        "agent_case_diagnostics": parsed.get("agent_case_diagnostics"),
        "input_type": parsed.get("input_type"),
        "source_input": parsed.get("source_input"),
        "api_execution_policy": parsed.get("api_execution_policy"),
        "allow_out_of_schema_api_cases": parsed.get("allow_out_of_schema_api_cases"),
        "api_path_prefix_rewrite": parsed.get("api_path_prefix_rewrite"),
        "rag_context": parsed.get("rag_context"),
        "rag_retrieval": parsed.get("rag_retrieval"),
        "cancelled": parsed.get("cancelled", False),
        "cancelled_at": parsed.get("cancelled_at"),
        "last_error": parsed.get("last_error"),
    }
    return base

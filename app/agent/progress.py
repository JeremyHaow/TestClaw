import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redaction import redact_sensitive_data
from app.models.task import Task, TaskStatus

logger = logging.getLogger(__name__)

EXECUTION_LOG_KEYS = (
    "execution_result",
    "test_plan",
    "test_cases",
    "workflow_steps",
    "bug_report",
    "api_plan",
    "ui_plan",
    "api_cases",
    "api_cases_generated",
    "api_case_generation_source",
    "api_coverage_goal",
    "ui_cases",
    "api_execution_result",
    "ui_execution_result",
    "final_report",
    "artifacts",
    "tool_registry",
    "skill_plan",
    "tool_calls",
    "tool_summary",
    "agent_mission_plan",
    "agent_roster",
    "agent_delegation_trace",
    "agent_react_trace",
    "agent_actions",
    "agent_action_observations",
    "agent_action_diagnostics",
    "agent_tool_calls",
    "agent_observations",
    "agent_evidence",
    "agent_protocol_evaluations",
    "agent_protocol_summary",
    "evidence_evaluation",
    "agent_evaluations",
    "agent_attempt_history",
    "agent_execution_stage",
    "agent_next_node",
    "agent_replan_counts",
    "agent_replan_feedback",
    "agent_retry_counts",
    "agent_retry_feedback",
    "agent_human_question",
    "agent_strategy_decision",
    "agent_tool_plan",
    "agent_strategy_diagnostics",
    "agent_case_diagnostics",
    "input_type",
    "source_input",
    "ui_seed_url",
    "base_url_override",
    "api_execution_policy",
    "allow_out_of_schema_api_cases",
    "api_path_prefix_rewrite",
    "auth_headers",
    "custom_headers",
    "auth_preflight",
    "auth_discovery",
    "current_step",
    "progress_events",
    "cancelled",
    "cancelled_at",
    "last_error",
    "scene_hints",
    "auth_chain",
    "rag_context",
    "rag_retrieval",
    "setup_instructions",
    "setup_result",
    "login_instructions",
    "login_result",
    "login_verified",
    "authenticated_ui_context",
    "ui_login_snapshot",
    "ui_login_screenshot",
    "login_playwright_commands",
    "ui_captcha_result",
    "ui_reproducible_script",
    "ui_execution_context_plan",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_execution_log(log: str | None) -> dict[str, Any]:
    if not log:
        return {}
    try:
        parsed = json.loads(log)
    except Exception:
        return {"raw_log": log}
    return parsed if isinstance(parsed, dict) else {"raw_log": parsed}


def _merge_unique(previous: Any, current: Any) -> list[Any]:
    merged: list[Any] = []
    seen: set[str] = set()
    for item in [*(previous or []), *(current or [])]:
        marker = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        if marker in seen:
            continue
        seen.add(marker)
        merged.append(item)
    return merged


def append_progress_event(state: dict[str, Any], node: str, status: str, detail: str) -> dict[str, Any]:
    event = {
        "node": node,
        "status": status,
        "detail": detail,
        "timestamp": utc_now_iso(),
    }
    events = state.setdefault("progress_events", [])
    events.append(event)
    if len(events) > 200:
        del events[:-200]
    state["current_step"] = event
    return event


def build_execution_log_payload(
    state: dict[str, Any],
    previous_log: str | None = None,
) -> dict[str, Any]:
    previous = parse_execution_log(previous_log)
    payload: dict[str, Any] = {}

    for key in EXECUTION_LOG_KEYS:
        if key in ("workflow_steps", "progress_events"):
            payload[key] = _merge_unique(previous.get(key), state.get(key))
        elif key in state and state.get(key) is not None:
            payload[key] = state.get(key)
        elif key in previous:
            payload[key] = previous.get(key)

    if previous.get("cancelled") and not payload.get("cancelled"):
        payload["cancelled"] = True
        payload["cancelled_at"] = previous.get("cancelled_at")

    return redact_sensitive_data(payload)


def dumps_execution_log(state: dict[str, Any], previous_log: str | None = None) -> str:
    return json.dumps(
        build_execution_log_payload(state, previous_log=previous_log),
        ensure_ascii=False,
        default=str,
    )


def latest_workflow_step(state: dict[str, Any], node: str | None = None) -> dict[str, Any] | None:
    steps = state.get("workflow_steps") or []
    for step in reversed(steps):
        if not node or step.get("node") == node:
            return step
    return None


def determine_final_status(state: dict[str, Any]) -> TaskStatus:
    if state.get("cancelled"):
        return TaskStatus.CANCELLED

    api_result = state.get("api_execution_result")
    ui_result = state.get("ui_execution_result")
    execution_result = state.get("execution_result")

    # Check if anything actually ran
    api_ran = api_result is not None and api_result.get("completed", 0) > 0
    ui_ran = ui_result is not None and ui_result.get("completed", 0) > 0
    legacy_ran = execution_result is not None

    nothing_ran = not api_ran and not ui_ran and not legacy_ran
    if nothing_ran:
        # Check if there was a hard error
        if state.get("last_error"):
            return TaskStatus.FAILED
        return TaskStatus.FAILED

    # Default: absent result = not applicable (pass)
    api_passed = (api_result or {}).get("all_passed", True)
    ui_passed = (ui_result or {}).get("all_passed", True)
    legacy_passed = True
    if execution_result is not None:
        legacy_passed = (execution_result or {}).get("status_code", 0) == 0

    if api_passed and ui_passed and legacy_passed:
        return TaskStatus.SUCCEEDED

    # Check if at least some tests passed (partial success = BUG_FOUND, not FAILED)
    api_any_passed = (api_result or {}).get("passed", 0) > 0
    ui_any_passed = (ui_result or {}).get("passed", 0) > 0
    any_success = api_any_passed or ui_any_passed

    if any_success or state.get("last_error"):
        return TaskStatus.BUG_FOUND
    return TaskStatus.FAILED


async def persist_task_state(
    db: AsyncSession,
    task: Task,
    state: dict[str, Any],
    status: TaskStatus | None = None,
    refresh: bool = False,
) -> Task:
    current_status = task.status if isinstance(task.status, str) else task.status.value
    if current_status == TaskStatus.CANCELLED.value and status != TaskStatus.CANCELLED:
        state["cancelled"] = True
        status = TaskStatus.CANCELLED

    task.generated_code = state.get("generated_code")
    task.execution_log = dumps_execution_log(state, previous_log=task.execution_log)
    if status is not None:
        task.status = status

    await db.commit()
    if refresh:
        await db.refresh(task)
    return task


async def persist_progress(
    state: dict[str, Any],
    node: str,
    status: str,
    detail: str,
    task_status: TaskStatus | None = None,
) -> None:
    db = state.get("db_session")
    task_id = state.get("task_id")
    if not db or not task_id:
        append_progress_event(state, node, status, detail)
        return

    append_progress_event(state, node, status, detail)

    try:
        task = await db.get(Task, task_id)
        if task is None:
            return
        current_status = task.status if isinstance(task.status, str) else task.status.value
        if current_status == TaskStatus.CANCELLED.value and task_status != TaskStatus.CANCELLED:
            state["cancelled"] = True
            return
        await persist_task_state(db, task, state, status=task_status)
    except Exception as exc:
        logger.warning("Failed to persist progress for task %s: %s", task_id, exc)


async def mark_task_cancelled(db: AsyncSession, task: Task, detail: str) -> Task:
    state = parse_execution_log(task.execution_log)
    state["cancelled"] = True
    state["cancelled_at"] = utc_now_iso()
    state.setdefault("workflow_steps", []).append(
        {"node": "cancel", "status": "cancelled", "detail": detail}
    )
    append_progress_event(state, "cancel", "cancelled", detail)
    return await persist_task_state(db, task, state, status=TaskStatus.CANCELLED, refresh=True)

import asyncio
import json
import logging
from typing import Any

from sqlalchemy import select

from app.agent.graph import agent_graph
from app.agent.progress import (
    determine_final_status,
    latest_workflow_step,
    persist_progress,
    persist_task_state,
)
from app.database import AsyncSessionLocal
from app.models.bug_report import BugReport
from app.models.task import TaskStatus
from app.services.task_service import normalize_agent_test_type, task_service
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


def _coerce_text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        lines = []
        for index, item in enumerate(value, start=1):
            text = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
            lines.append(f"{index}. {text}")
        return "\n".join(lines) or fallback
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


@celery_app.task(bind=True, name="run_agent_task")
def run_agent_task(self, task_id: str, objective: str, target_url: str, **kwargs):
    logger.info("[Agent] Starting task %s", task_id)
    return asyncio.run(_run(task_id, objective, target_url, **kwargs))


async def run_graph_with_progress(state: dict[str, Any]) -> dict[str, Any]:
    """Run the LangGraph agent and persist a snapshot after each node update."""
    final_state = dict(state)
    await persist_progress(
        final_state,
        "agent",
        "running",
        "Agent execution started",
        task_status=TaskStatus.RUNNING,
    )

    try:
        async for chunk in agent_graph.astream(final_state, stream_mode="updates"):
            for node_name, node_state in chunk.items():
                if isinstance(node_state, dict):
                    final_state.update(node_state)
                step = latest_workflow_step(final_state, node_name)
                step_status = (step or {}).get("status", "done")
                detail = (step or {}).get("detail") or f"{node_name} completed"
                await persist_progress(final_state, node_name, step_status, detail)
                if final_state.get("cancelled"):
                    return final_state
    except Exception as exc:
        final_state["last_error"] = str(exc)
        final_state.setdefault("workflow_steps", []).append(
            {"node": "agent", "status": "failed", "detail": str(exc)}
        )
        await persist_progress(final_state, "agent", "failed", str(exc), task_status=TaskStatus.FAILED)
        raise

    return final_state


async def _run(task_id: str, objective: str, target_url: str, **kwargs: Any):
    # Dispose engine to clear connections from previous event loops (Celery + asyncio.run issue)
    from app.database import engine
    await engine.dispose()

    async with AsyncSessionLocal() as db:
        task = await task_service.get(db, task_id)
        if task:
            current_status = task.status if isinstance(task.status, str) else task.status.value
            if current_status == TaskStatus.CANCELLED.value:
                return {"task_id": task_id, "cancelled": True, "workflow_steps": []}
            await task_service.update_status(db, task, TaskStatus.RUNNING)
        if task:
            kwargs.setdefault("api_doc_id", task.api_doc_id)
            kwargs.setdefault("environment_id", task.environment_id)

        # Extract custom params from kwargs
        auth_headers = kwargs.pop("auth_headers", None)
        custom_headers = kwargs.pop("custom_headers", None)
        auth_config = kwargs.pop("auth_config", None)
        base_url_override = kwargs.pop("base_url_override", None)
        api_execution_policy = kwargs.pop("api_execution_policy", None)
        setup_instructions = kwargs.pop("setup_instructions", None)
        login_instructions = kwargs.pop("login_instructions", None)
        test_type = normalize_agent_test_type(kwargs.pop("test_type", None), default="full")

        state = {
            "task_id": task_id,
            "objective": objective,
            "target_url": target_url,
            "test_type": test_type,
            "retry_count": 0,
            "messages": [],
            "workflow_steps": [],
            "db_session": db,
            **kwargs,
        }

        merged_headers = {}
        if isinstance(custom_headers, dict):
            merged_headers.update(custom_headers)
            state["custom_headers"] = custom_headers
        if isinstance(auth_headers, dict):
            merged_headers.update(auth_headers)
        if isinstance(auth_config, dict) and auth_config.get("enabled"):
            state["auth_config"] = auth_config
        if merged_headers:
            state["auth_headers"] = merged_headers
        if base_url_override:
            state["base_url_override"] = base_url_override
        if api_execution_policy:
            state["api_execution_policy"] = api_execution_policy
        setup_value = setup_instructions or login_instructions
        if setup_value:
            state["setup_instructions"] = setup_value
            state["login_instructions"] = setup_value

        final_state = await run_graph_with_progress(state)

        if task:
            await db.refresh(task)
            final_status = determine_final_status(final_state)
            current_status = task.status if isinstance(task.status, str) else task.status.value
            if current_status == TaskStatus.CANCELLED.value:
                final_state["cancelled"] = True
                final_status = TaskStatus.CANCELLED
            await persist_task_state(db, task, final_state, status=final_status, refresh=True)

            bug_report = final_state.get("bug_report")
            current_status = task.status if isinstance(task.status, str) else task.status.value
            if bug_report and current_status != TaskStatus.CANCELLED.value:
                existing = (
                    await db.execute(select(BugReport).where(BugReport.task_id == task_id))
                ).scalar_one_or_none()
                if existing is None:
                    try:
                        db.add(
                            BugReport(
                                task_id=task_id,
                                title=_coerce_text(
                                    bug_report.get("title"), "Automated test failure detected"
                                )[:255],
                                root_cause=_coerce_text(
                                    bug_report.get("root_cause"), "Unknown root cause"
                                ),
                                reproduce_steps=_coerce_text(
                                    bug_report.get("reproduce_steps"), "Run the task again."
                                ),
                                error_logs=_coerce_text(
                                    (final_state.get("execution_result") or {}).get("stderr"), ""
                                ),
                                fix_suggestion=_coerce_text(bug_report.get("fix_suggestion"), ""),
                            )
                        )
                        await db.commit()
                    except Exception as exc:
                        await db.rollback()
                        logger.warning("Failed to persist bug report for task %s: %s", task_id, exc)

        # Remove non-serializable db_session before returning to Celery
        final_state.pop("db_session", None)
        final_state.pop("auth_config", None)
        return final_state

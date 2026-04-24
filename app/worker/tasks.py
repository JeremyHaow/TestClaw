import asyncio
import json
import logging

from sqlalchemy import select

from app.agent.graph import agent_graph
from app.database import AsyncSessionLocal
from app.models.bug_report import BugReport
from app.models.task import TaskStatus
from app.services.task_service import task_service
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="run_agent_task")
def run_agent_task(self, task_id: str, objective: str, target_url: str, **kwargs):
    logger.info("[Agent] Starting task %s", task_id)
    return asyncio.run(_run(task_id, objective, target_url, **kwargs))


async def _run(task_id: str, objective: str, target_url: str, **kwargs):
    async with AsyncSessionLocal() as db:
        task = await task_service.get(db, task_id)
        if task:
            await task_service.update_status(db, task, TaskStatus.RUNNING)
        if task:
            kwargs.setdefault("api_doc_id", task.api_doc_id)
            kwargs.setdefault("environment_id", task.environment_id)

        state = {
            "task_id": task_id,
            "objective": objective,
            "target_url": target_url,
            "retry_count": 0,
            "messages": [],
            "workflow_steps": [],
            "db_session": db,
            **kwargs,
        }

        final_state = await agent_graph.ainvoke(state)

        if task:
            task.generated_code = final_state.get("generated_code")
            task.execution_log = json.dumps(
                {
                    "execution_result": final_state.get("execution_result"),
                    "test_plan": final_state.get("test_plan"),
                    "test_cases": final_state.get("test_cases"),
                    "workflow_steps": final_state.get("workflow_steps", []),
                    "bug_report": final_state.get("bug_report"),
                    "api_plan": final_state.get("api_plan"),
                    "ui_plan": final_state.get("ui_plan"),
                    "api_cases": final_state.get("api_cases"),
                    "ui_cases": final_state.get("ui_cases"),
                    "api_execution_result": final_state.get("api_execution_result"),
                    "ui_execution_result": final_state.get("ui_execution_result"),
                    "final_report": final_state.get("final_report"),
                    "artifacts": final_state.get("artifacts"),
                    "input_type": final_state.get("input_type"),
                    "source_input": final_state.get("source_input"),
                },
                ensure_ascii=False,
                default=str,
            )

            # Determine final status
            api_result = final_state.get("api_execution_result")
            ui_result = final_state.get("ui_execution_result")
            execution_result = final_state.get("execution_result")

            # Check overall pass/fail
            api_passed = (api_result or {}).get("all_passed", True)
            ui_passed = (ui_result or {}).get("all_passed", True)
            legacy_passed = (execution_result or {}).get("status_code", 0) == 0

            all_passed = api_passed and ui_passed and legacy_passed

            if all_passed:
                task.status = TaskStatus.SUCCEEDED
            elif final_state.get("last_error"):
                task.status = TaskStatus.BUG_FOUND
            else:
                task.status = TaskStatus.FAILED

            bug_report = final_state.get("bug_report")
            if bug_report:
                existing = (
                    await db.execute(select(BugReport).where(BugReport.task_id == task_id))
                ).scalar_one_or_none()
                if existing is None:
                    db.add(
                        BugReport(
                            task_id=task_id,
                            title=bug_report.get("title", "Automated test failure detected"),
                            root_cause=bug_report.get("root_cause", "Unknown root cause"),
                            reproduce_steps=bug_report.get("reproduce_steps", "Run the task again."),
                            error_logs=(execution_result or {}).get("stderr"),
                            fix_suggestion=bug_report.get("fix_suggestion"),
                        )
                    )
            await db.commit()
            await db.refresh(task)

        return final_state

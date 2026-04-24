import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from starlette.responses import JSONResponse

from app.agent.graph import agent_graph
from app.core.dependencies import CurrentUser, DbSession
from app.models.task import TaskStatus
from app.schemas.task import TaskRead, parse_task_detail
from app.services.task_service import task_service
from app.worker.tasks import run_agent_task

logger = logging.getLogger(__name__)

router = APIRouter()


class RunCreate(BaseModel):
    source: str  # URL, Swagger URL, or Swagger JSON/YAML text
    test_type: str = "auto"  # auto, api, ui
    objective: str = ""  # optional objective description
    base_url: str | None = None  # optional base URL override
    headers: dict | None = None  # optional headers injection
    token: str | None = None  # optional auth token


@router.post("", response_model=TaskRead)
async def create_run(payload: RunCreate, db: DbSession, _: CurrentUser):
    """Create a new test run from source input (URL, Swagger URL, or Swagger text)."""
    from app.agent.nodes.source_loader import classify_input

    source = payload.source.strip()
    input_type = classify_input(source)

    # Determine target_url
    if payload.base_url:
        target_url = payload.base_url
    elif input_type == "url":
        target_url = source
    else:
        target_url = source  # Will be resolved by source_loader

    objective = payload.objective or f"Auto test from {input_type}"

    task = await task_service.create(
        db,
        objective=objective,
        target_url=target_url,
        test_type=payload.test_type,
        status=TaskStatus.QUEUED,
    )

    try:
        run_agent_task.delay(
            task.id,
            objective,
            target_url,
            test_type=payload.test_type,
            source_input=source,
        )
    except Exception as e:
        logger.warning("Celery dispatch failed: %s, running synchronously", e)
        final_state = await agent_graph.ainvoke(
            {
                "task_id": task.id,
                "objective": objective,
                "target_url": target_url,
                "test_type": payload.test_type,
                "source_input": source,
                "retry_count": 0,
                "messages": [],
                "workflow_steps": [],
                "db_session": db,
            }
        )
        await _persist_state(db, task, final_state)

    return task


@router.get("", response_model=list[TaskRead])
async def list_runs(
    db: DbSession, _: CurrentUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: str | None = Query(default=None),
    test_type: str | None = Query(default=None),
):
    """List all test runs with optional filters."""
    items, total = await task_service.list(
        db, page=page, page_size=page_size, status=status, test_type=test_type
    )
    return JSONResponse(
        content=[TaskRead.model_validate(i).model_dump(mode="json") for i in items],
        headers={"X-Total-Count": str(total)},
    )


@router.get("/{run_id}")
async def get_run_detail(run_id: str, db: DbSession, _: CurrentUser):
    """Get full run detail including plan, cases, API results, UI results, screenshots, summary."""
    task = await task_service.get(db, run_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Run not found")
    detail = parse_task_detail(task)

    # Enrich with additional fields from execution_log
    log_str = getattr(task, "execution_log", None) or ""
    try:
        parsed = json.loads(log_str) if log_str else {}
    except Exception:
        parsed = {}

    detail["api_plan"] = parsed.get("api_plan")
    detail["ui_plan"] = parsed.get("ui_plan")
    detail["api_cases"] = parsed.get("api_cases")
    detail["ui_cases"] = parsed.get("ui_cases")
    detail["api_execution_result"] = parsed.get("api_execution_result")
    detail["ui_execution_result"] = parsed.get("ui_execution_result")
    detail["final_report"] = parsed.get("final_report")
    detail["artifacts"] = parsed.get("artifacts")
    detail["input_type"] = parsed.get("input_type")
    detail["source_input"] = parsed.get("source_input")

    return detail


@router.get("/{run_id}/stream")
async def stream_run(run_id: str, db: DbSession, token: str | None = Query(default=None)):
    """SSE stream for real-time run progress updates."""
    if token:
        from app.core.security import decode_access_token
        from app.models.user import User
        from sqlalchemy import select
        try:
            payload = decode_access_token(token)
            username = payload.get("sub")
            if not username:
                raise HTTPException(status_code=401, detail="Invalid token")
            result = await db.execute(select(User).where(User.username == username, User.is_active.is_(True)))
            user = result.scalar_one_or_none()
            if user is None:
                raise HTTPException(status_code=401, detail="User not found")
        except ValueError:
            raise HTTPException(status_code=401, detail="Invalid token")
    else:
        raise HTTPException(status_code=401, detail="Token required for SSE stream")

    async def event_stream():
        last_status = None
        last_log = ""
        while True:
            task = await task_service.get(db, run_id)
            if task is None:
                yield f"data: {json.dumps({'error': 'Run not found'})}\n\n"
                break

            current_status = task.status if isinstance(task.status, str) else task.status.value
            current_log = task.execution_log or ""

            if current_status != last_status:
                yield f"data: {json.dumps({'run_id': run_id, 'type': 'status', 'status': current_status})}\n\n"
                last_status = current_status

            if current_log != last_log:
                # Send workflow steps if available
                try:
                    log_data = json.loads(current_log)
                    steps = log_data.get("workflow_steps", [])
                    if steps:
                        yield f"data: {json.dumps({'run_id': run_id, 'type': 'workflow', 'steps': steps})}\n\n"
                except Exception:
                    pass
                yield f"data: {json.dumps({'run_id': run_id, 'type': 'log', 'log': current_log[:2000]})}\n\n"
                last_log = current_log

            if current_status in ("succeeded", "failed", "bug_found", "cancelled"):
                yield f"data: {json.dumps({'run_id': run_id, 'type': 'done', 'status': current_status})}\n\n"
                break

            await asyncio.sleep(2)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/{run_id}/rerun", response_model=TaskRead)
async def rerun_run(run_id: str, db: DbSession, _: CurrentUser):
    """Re-run a previous test run."""
    task = await task_service.get(db, run_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Run not found")

    # Extract source_input from previous execution log
    source_input = ""
    try:
        log_data = json.loads(task.execution_log or "{}")
        source_input = log_data.get("source_input", "")
    except Exception:
        pass

    new_task = await task_service.create(
        db,
        objective=task.objective,
        target_url=task.target_url,
        test_type=task.test_type if isinstance(task.test_type, str) else task.test_type.value,
        status=TaskStatus.QUEUED,
    )
    try:
        run_agent_task.delay(
            new_task.id,
            new_task.objective,
            new_task.target_url,
            test_type=new_task.test_type if isinstance(new_task.test_type, str) else new_task.test_type.value,
            source_input=source_input,
        )
    except Exception as e:
        logger.warning("Celery dispatch failed on rerun: %s", e)
    return new_task


@router.post("/{run_id}/cancel")
async def cancel_run(run_id: str, db: DbSession, _: CurrentUser):
    """Cancel a running test run."""
    task = await task_service.get(db, run_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Run not found")
    current_status = task.status if isinstance(task.status, str) else task.status.value
    if current_status not in ("queued", "running"):
        raise HTTPException(status_code=400, detail="Run is not in a cancellable state")
    try:
        from app.worker.celery_app import celery_app
        celery_app.control.revoke(run_id, terminate=True)
    except Exception as e:
        logger.warning("Celery revoke failed for run %s: %s", run_id, e)
    await task_service.update_status(db, task, TaskStatus.FAILED)
    task.execution_log = '{"cancelled": true}'
    await db.commit()
    return {"message": "Run cancelled"}


@router.delete("/{run_id}")
async def delete_run(run_id: str, db: DbSession, _: CurrentUser):
    """Delete a test run."""
    task = await task_service.get(db, run_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Run not found")
    await db.delete(task)
    await db.commit()
    return {"message": "deleted"}


async def _persist_state(db, task, final_state: dict):
    """Persist agent state to task execution_log."""
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
    status_code = (final_state.get("execution_result") or {}).get("status_code")
    task.status = TaskStatus.SUCCEEDED if status_code == 0 else TaskStatus.FAILED
    if final_state.get("last_error") and status_code != 0:
        task.status = TaskStatus.BUG_FOUND
    await db.commit()
    await db.refresh(task)

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from starlette.responses import JSONResponse

from app.agent.progress import determine_final_status, mark_task_cancelled, persist_task_state
from app.core.dependencies import CurrentUser, DbSession
from app.core.redaction import redact_json_text, redact_sensitive_data
from app.database import AsyncSessionLocal
from app.models.bug_report import BugReport
from app.models.task import TaskStatus
from app.schemas.task import TaskCreate, TaskRead, parse_task_detail
from app.services.task_service import normalize_agent_test_type, task_service
from app.worker.tasks import run_agent_task, run_graph_with_progress
from sqlalchemy import select

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("", response_model=TaskRead)
async def create_task(payload: TaskCreate, db: DbSession, _: CurrentUser):
    task = await task_service.create(
        db,
        objective=payload.objective,
        target_url=payload.target_url,
        test_type=payload.test_type,
        api_doc_id=payload.api_doc_id,
        environment_id=payload.environment_id,
        status=TaskStatus.QUEUED,
    )
    try:
        run_agent_task.delay(
            task.id,
            payload.objective,
            payload.target_url,
            test_type=normalize_agent_test_type(payload.test_type),
            api_doc_id=payload.api_doc_id,
            environment_id=payload.environment_id,
        )
    except Exception as e:
        logger.warning("Celery dispatch failed: %s", e)
        # Build source_input from api_doc if available, otherwise use target_url
        source_input = payload.target_url or ""
        if payload.api_doc_id:
            from app.models.api_document import ApiDocument
            doc = await db.get(ApiDocument, payload.api_doc_id)
            if doc and doc.raw_content:
                source_input = doc.raw_content
        final_state = await run_graph_with_progress(
            {
                "task_id": task.id,
                "objective": payload.objective,
                "target_url": payload.target_url,
                "test_type": normalize_agent_test_type(payload.test_type),
                "source_input": source_input,
                "api_doc_id": payload.api_doc_id,
                "environment_id": payload.environment_id,
                "retry_count": 0,
                "messages": [],
                "workflow_steps": [],
                "db_session": db,
            }
        )
        await persist_task_state(
            db,
            task,
            final_state,
            status=determine_final_status(final_state),
            refresh=True,
        )
    return task


@router.get("", response_model=list[TaskRead])
async def list_tasks(
    db: DbSession, _: CurrentUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: str | None = Query(default=None),
    test_type: str | None = Query(default=None),
):
    try:
        items, total = await task_service.list(
            db, page=page, page_size=page_size, status=status, test_type=test_type
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(
        content=[TaskRead.model_validate(i).model_dump(mode="json") for i in items],
        headers={"X-Total-Count": str(total)},
    )


@router.get("/{task_id}")
async def get_task(task_id: str, db: DbSession, _: CurrentUser):
    task = await task_service.get(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return parse_task_detail(task)


@router.get("/{task_id}/bug-report")
async def get_bug_report(task_id: str, db: DbSession, _: CurrentUser):
    result = await db.execute(select(BugReport).where(BugReport.task_id == task_id))
    report = result.scalars().first()
    if report is None:
        raise HTTPException(status_code=404, detail="Bug report not found")
    return report


@router.post("/{task_id}/rerun", response_model=TaskRead)
async def rerun_task(task_id: str, db: DbSession, _: CurrentUser):
    task = await task_service.get(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    new_task = await task_service.create(
        db,
        objective=task.objective,
        target_url=task.target_url,
        test_type=task.test_type,
        api_doc_id=task.api_doc_id,
        environment_id=task.environment_id,
        status=TaskStatus.QUEUED,
    )
    try:
        run_agent_task.delay(
            new_task.id,
            new_task.objective,
            new_task.target_url,
            test_type=normalize_agent_test_type(new_task.test_type),
            api_doc_id=new_task.api_doc_id,
            environment_id=new_task.environment_id,
        )
    except Exception as e:
        logger.warning("Celery dispatch failed on rerun: %s", e)
    return new_task


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str, db: DbSession, _: CurrentUser):
    task = await task_service.get(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    current_status = task.status if isinstance(task.status, str) else task.status.value
    if current_status not in ("queued", "running"):
        raise HTTPException(status_code=400, detail="Task is not in a cancellable state")
    try:
        from app.worker.celery_app import celery_app
        celery_app.control.revoke(task_id, terminate=True)
    except Exception as e:
        logger.warning("Celery revoke failed for task %s: %s", task_id, e)
    await mark_task_cancelled(db, task, "Task cancelled by user")
    return {"message": "Task cancelled"}


@router.delete("/{task_id}")
async def delete_task(task_id: str, db: DbSession, _: CurrentUser):
    task = await task_service.get(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    await db.delete(task)
    await db.commit()
    return {"message": "deleted"}


@router.get("/{task_id}/stream")
async def stream_task(task_id: str, db: DbSession, token: str | None = Query(default=None)):
    # Support token in query params for EventSource (can't set headers)
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
            async with AsyncSessionLocal() as stream_db:
                task = await task_service.get(stream_db, task_id)
                if task is None:
                    yield f"data: {json.dumps({'error': 'Task not found'})}\n\n"
                    break
                current_status = task.status if isinstance(task.status, str) else task.status.value
                current_log = task.execution_log or ""

            if current_status != last_status:
                yield f"data: {json.dumps({'task_id': task_id, 'type': 'status', 'status': current_status})}\n\n"
                last_status = current_status

            if current_log != last_log:
                try:
                    log_data = json.loads(current_log)
                    log_data = redact_sensitive_data(log_data)
                    yield f"data: {json.dumps({'task_id': task_id, 'type': 'snapshot', 'snapshot': log_data})}\n\n"
                except Exception:
                    pass
                safe_log = redact_json_text(current_log) or current_log
                yield f"data: {json.dumps({'task_id': task_id, 'type': 'log', 'log': safe_log})}\n\n"
                last_log = current_log

            if current_status in ("succeeded", "failed", "bug_found", "cancelled"):
                yield f"data: {json.dumps({'task_id': task_id, 'type': 'done', 'status': current_status})}\n\n"
                break

            await asyncio.sleep(2)

    return StreamingResponse(event_stream(), media_type="text/event-stream")

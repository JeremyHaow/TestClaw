import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from starlette.responses import JSONResponse

from app.agent.graph import agent_graph
from app.core.dependencies import CurrentUser, DbSession
from app.models.bug_report import BugReport
from app.models.task import TaskStatus
from app.schemas.task import TaskCreate, TaskRead, parse_task_detail
from app.services.task_service import task_service
from app.worker.tasks import run_agent_task
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
            test_type=payload.test_type,
            api_doc_id=payload.api_doc_id,
            environment_id=payload.environment_id,
        )
    except Exception as e:
        logger.warning("Celery dispatch failed: %s", e)
        final_state = await agent_graph.ainvoke(
            {
                "task_id": task.id,
                "objective": payload.objective,
                "target_url": payload.target_url,
                "test_type": payload.test_type,
                "api_doc_id": payload.api_doc_id,
                "environment_id": payload.environment_id,
                "retry_count": 0,
                "messages": [],
                "workflow_steps": [],
                "db_session": db,
            }
        )
        task.generated_code = final_state.get("generated_code")
        task.execution_log = json.dumps(
            {
                "execution_result": final_state.get("execution_result"),
                "test_plan": final_state.get("test_plan"),
                "test_cases": final_state.get("test_cases"),
                "workflow_steps": final_state.get("workflow_steps", []),
                "bug_report": final_state.get("bug_report"),
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
    return task


@router.get("", response_model=list[TaskRead])
async def list_tasks(
    db: DbSession, _: CurrentUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: str | None = Query(default=None),
    test_type: str | None = Query(default=None),
):
    items, total = await task_service.list(
        db, page=page, page_size=page_size, status=status, test_type=test_type
    )
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
        test_type=task.test_type if isinstance(task.test_type, str) else task.test_type.value,
        api_doc_id=task.api_doc_id,
        environment_id=task.environment_id,
        status=TaskStatus.QUEUED,
    )
    try:
        run_agent_task.delay(
            new_task.id,
            new_task.objective,
            new_task.target_url,
            test_type=new_task.test_type if isinstance(new_task.test_type, str) else new_task.test_type.value,
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
    await task_service.update_status(db, task, TaskStatus.FAILED)
    task.execution_log = '{"cancelled": true}'
    await db.commit()
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
            task = await task_service.get(db, task_id)
            if task is None:
                yield f"data: {json.dumps({'error': 'Task not found'})}\n\n"
                break

            current_status = task.status if isinstance(task.status, str) else task.status.value
            current_log = task.execution_log or ""

            if current_status != last_status:
                yield f"data: {json.dumps({'task_id': task_id, 'type': 'status', 'status': current_status})}\n\n"
                last_status = current_status

            if current_log != last_log:
                yield f"data: {json.dumps({'task_id': task_id, 'type': 'log', 'log': current_log})}\n\n"
                last_log = current_log

            if current_status in ("succeeded", "failed", "bug_found", "cancelled"):
                yield f"data: {json.dumps({'task_id': task_id, 'type': 'done', 'status': current_status})}\n\n"
                break

            await asyncio.sleep(2)

    return StreamingResponse(event_stream(), media_type="text/event-stream")

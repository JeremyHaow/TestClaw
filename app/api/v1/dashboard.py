import json
from datetime import datetime, timedelta

from sqlalchemy import func, select
from fastapi import APIRouter

from app.core.dependencies import CurrentUser, DbSession
from app.models.api_document import ApiDocument
from app.models.environment import Environment
from app.models.llm_provider import LLMProvider
from app.models.task import Task, TaskStatus
from app.models.test_case import TestCase
from app.schemas.dashboard import DashboardSummary

router = APIRouter()


@router.get("/summary", response_model=DashboardSummary)
async def get_dashboard_summary(db: DbSession, _: CurrentUser):
    total_tasks = (await db.execute(select(func.count()).select_from(Task))).scalar_one()
    total_documents = (await db.execute(select(func.count()).select_from(ApiDocument))).scalar_one()
    total_providers = (await db.execute(select(func.count()).select_from(LLMProvider))).scalar_one()
    total_environments = (await db.execute(select(func.count()).select_from(Environment))).scalar_one()

    status_counts = {}
    for status in TaskStatus:
        count = (
            await db.execute(select(func.count()).select_from(Task).where(Task.status == status))
        ).scalar_one()
        status_counts[status.value] = count

    recent_tasks_result = await db.execute(select(Task).order_by(Task.created_at.desc()).limit(10))
    recent_tasks = [
        {
            "id": task.id,
            "objective": task.objective,
            "status": task.status.value if hasattr(task.status, "value") else str(task.status),
            "target_url": task.target_url,
            "test_type": task.test_type.value if hasattr(task.test_type, "value") else str(task.test_type),
            "created_at": task.created_at.isoformat() if task.created_at else None,
        }
        for task in recent_tasks_result.scalars()
    ]
    return DashboardSummary(
        total_tasks=total_tasks,
        total_documents=total_documents,
        total_providers=total_providers,
        total_environments=total_environments,
        recent_tasks=recent_tasks,
        tasks_by_status=status_counts,
    )


@router.get("/stats")
async def get_dashboard_stats(db: DbSession, _: CurrentUser):
    total_tasks = (await db.execute(select(func.count()).select_from(Task))).scalar_one()
    total_cases = (await db.execute(select(func.count()).select_from(TestCase))).scalar_one()
    total_envs = (await db.execute(select(func.count()).select_from(Environment))).scalar_one()
    total_docs = (await db.execute(select(func.count()).select_from(ApiDocument))).scalar_one()

    succeeded = (
        await db.execute(select(func.count()).select_from(Task).where(Task.status == TaskStatus.SUCCEEDED))
    ).scalar_one()
    failed = (
        await db.execute(select(func.count()).select_from(Task).where(Task.status == TaskStatus.FAILED))
    ).scalar_one()
    bug_found = (
        await db.execute(select(func.count()).select_from(Task).where(Task.status == TaskStatus.BUG_FOUND))
    ).scalar_one()
    completed = succeeded + failed + bug_found
    pass_rate = round(succeeded / completed * 100, 1) if completed > 0 else 0

    # AI generated cases (source == 'ai')
    ai_cases = (
        await db.execute(select(func.count()).select_from(TestCase).where(TestCase.source == "ai"))
    ).scalar_one()

    # Status breakdown
    status_counts = {}
    for status in TaskStatus:
        count = (
            await db.execute(select(func.count()).select_from(Task).where(Task.status == status))
        ).scalar_one()
        status_counts[status.value] = count

    # Recent 7 days task trend
    now = datetime.utcnow()
    trend = []
    for i in range(6, -1, -1):
        day = now - timedelta(days=i)
        day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        count = (
            await db.execute(
                select(func.count()).select_from(Task).where(
                    Task.created_at >= day_start, Task.created_at < day_end
                )
            )
        ).scalar_one()
        trend.append({"date": day_start.strftime("%m-%d"), "count": count})

    # Recent tasks
    recent_result = await db.execute(select(Task).order_by(Task.created_at.desc()).limit(5))
    recent_tasks = [
        {
            "id": t.id,
            "objective": t.objective,
            "status": t.status.value if hasattr(t.status, "value") else str(t.status),
            "test_type": t.test_type.value if hasattr(t.test_type, "value") else str(t.test_type),
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in recent_result.scalars()
    ]

    return {
        "total_tasks": total_tasks,
        "total_cases": total_cases,
        "total_envs": total_envs,
        "total_docs": total_docs,
        "pass_rate": pass_rate,
        "ai_cases": ai_cases,
        "succeeded": succeeded,
        "failed": failed,
        "bug_found": bug_found,
        "queued": status_counts.get("queued", 0),
        "running": status_counts.get("running", 0),
        "tasks_by_status": status_counts,
        "trend": trend,
        "recent_tasks": recent_tasks,
    }

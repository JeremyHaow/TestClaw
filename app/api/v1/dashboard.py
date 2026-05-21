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

_TASK_TERMINAL_STATES = {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.BUG_FOUND}


async def _get_status_counts(db: DbSession) -> dict[str, int]:
    """Single GROUP BY query for all task status counts."""
    result = await db.execute(
        select(Task.status, func.count()).group_by(Task.status)
    )
    counts = {s.value: 0 for s in TaskStatus}
    for status, count in result.all():
        key = status.value if hasattr(status, "value") else str(status)
        counts[key] = count
    return counts


@router.get("/summary", response_model=DashboardSummary)
async def get_dashboard_summary(db: DbSession, _: CurrentUser):
    total_tasks, total_documents, total_providers, total_environments = await _get_counts(db)
    status_counts = await _get_status_counts(db)

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


async def _get_counts(db: DbSession):
    """Run count queries sequentially on the request AsyncSession."""
    tasks_result = await db.execute(select(func.count()).select_from(Task))
    documents_result = await db.execute(select(func.count()).select_from(ApiDocument))
    providers_result = await db.execute(select(func.count()).select_from(LLMProvider))
    environments_result = await db.execute(select(func.count()).select_from(Environment))
    return (
        tasks_result.scalar_one(),
        documents_result.scalar_one(),
        providers_result.scalar_one(),
        environments_result.scalar_one(),
    )


@router.get("/stats")
async def get_dashboard_stats(db: DbSession, _: CurrentUser):
    counts_result = await db.execute(select(func.count()).select_from(Task))
    cases_result = await db.execute(select(func.count()).select_from(TestCase))
    envs_result = await db.execute(select(func.count()).select_from(Environment))
    docs_result = await db.execute(select(func.count()).select_from(ApiDocument))
    ai_result = await db.execute(select(func.count()).select_from(TestCase).where(TestCase.source.ilike("%ai%")))
    status_result = await db.execute(select(Task.status, func.count()).group_by(Task.status))

    total_tasks = counts_result.scalar_one()
    total_cases = cases_result.scalar_one()
    total_envs = envs_result.scalar_one()
    total_docs = docs_result.scalar_one()
    ai_cases = ai_result.scalar_one()

    status_counts = {s.value: 0 for s in TaskStatus}
    for status, count in status_result.all():
        key = status.value if hasattr(status, "value") else str(status)
        status_counts[key] = count

    succeeded = status_counts.get("succeeded", 0)
    failed = status_counts.get("failed", 0)
    bug_found = status_counts.get("bug_found", 0)
    completed = succeeded + failed + bug_found
    pass_rate = round(succeeded / completed * 100, 1) if completed > 0 else 0

    # Recent 7 days task trend — single query with date grouping
    now = datetime.utcnow()
    seven_days_ago = now - timedelta(days=7)
    trend_result = await db.execute(
        select(
            func.date(Task.created_at).label("day"),
            func.count().label("count"),
        )
        .where(Task.created_at >= seven_days_ago)
        .group_by(func.date(Task.created_at))
        .order_by(func.date(Task.created_at))
    )
    trend_map = {}
    for row in trend_result.all():
        day_str = str(row.day) if row.day else ""
        if day_str:
            # Extract MM-DD from date string
            try:
                dt = datetime.strptime(day_str, "%Y-%m-%d")
                trend_map[dt.strftime("%m-%d")] = row.count
            except ValueError:
                pass

    trend = []
    for i in range(6, -1, -1):
        day = now - timedelta(days=i)
        label = day.strftime("%m-%d")
        trend.append({"date": label, "count": trend_map.get(label, 0)})

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

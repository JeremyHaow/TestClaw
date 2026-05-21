from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bug_report import BugReport


class ReportService:
    async def get_bug_report(self, db: AsyncSession, task_id: str) -> BugReport | None:
        result = await db.execute(select(BugReport).where(BugReport.task_id == task_id))
        return result.scalars().first()


report_service = ReportService()

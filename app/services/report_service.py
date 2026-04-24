from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bug_report import BugReport
from app.models.task import TestRun


class ReportService:
    async def get_bug_report(self, db: AsyncSession, task_id: str) -> BugReport | None:
        return await db.get(BugReport, task_id)

    async def get_run_report(self, db: AsyncSession, run_id: str) -> TestRun | None:
        return await db.get(TestRun, run_id)


report_service = ReportService()

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task, TaskStatus


class TaskService:
    async def create(self, db: AsyncSession, **kwargs) -> Task:
        task = Task(**kwargs)
        db.add(task)
        await db.commit()
        await db.refresh(task)
        return task

    async def get(self, db: AsyncSession, task_id: str) -> Task | None:
        return await db.get(Task, task_id)

    async def list(
        self,
        db: AsyncSession,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
        test_type: str | None = None,
    ) -> tuple[list[Task], int]:
        from sqlalchemy import func

        base = select(Task)
        if status:
            base = base.where(Task.status == status)
        if test_type:
            base = base.where(Task.test_type == test_type)

        count_stmt = select(func.count()).select_from(base.subquery())
        total = (await db.execute(count_stmt)).scalar_one()

        offset = (page - 1) * page_size
        result = await db.execute(
            base.order_by(Task.created_at.desc()).offset(offset).limit(page_size)
        )
        return list(result.scalars()), total

    async def update_status(self, db: AsyncSession, task: Task, status: TaskStatus, execution_log: str | None = None) -> Task:
        task.status = status
        if execution_log is not None:
            task.execution_log = execution_log
        await db.commit()
        await db.refresh(task)
        return task


task_service = TaskService()

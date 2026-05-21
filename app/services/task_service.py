from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task, TaskStatus, TestType


TEST_TYPE_ALIASES = {
    "auto": TestType.AUTO,
    "api": TestType.API,
    "ui": TestType.UI,
    "functional": TestType.FUNCTIONAL,
    "full": TestType.FULL,
    "suite": TestType.SUITE,
}

STATUS_ALIASES = {
    alias: status
    for status in TaskStatus
    for alias in {status.value.lower(), status.name.lower()}
}


def normalize_test_type(value: str | TestType | None, default: TestType = TestType.FULL) -> TestType:
    if value is None or value == "":
        return default
    if isinstance(value, TestType):
        return value
    normalized = str(value).strip().lower()
    if normalized.startswith("testtype."):
        normalized = normalized.rsplit(".", 1)[-1].lower()
    try:
        return TEST_TYPE_ALIASES[normalized]
    except KeyError as exc:
        allowed = ", ".join(sorted(TEST_TYPE_ALIASES))
        raise ValueError(f"Unsupported test_type '{value}'. Expected one of: {allowed}") from exc


def normalize_agent_test_type(value: str | TestType | None, default: str = "full") -> str:
    return normalize_test_type(value, normalize_test_type(default)).value.lower()


def normalize_task_status(value: str | TaskStatus | None) -> TaskStatus | None:
    if value is None or value == "":
        return None
    if isinstance(value, TaskStatus):
        return value
    normalized = str(value).strip().lower()
    if normalized.startswith("taskstatus."):
        normalized = normalized.rsplit(".", 1)[-1].lower()
    try:
        return STATUS_ALIASES[normalized]
    except KeyError as exc:
        allowed = ", ".join(sorted(status.value for status in TaskStatus))
        raise ValueError(f"Unsupported status '{value}'. Expected one of: {allowed}") from exc


class TaskService:
    async def create(self, db: AsyncSession, **kwargs) -> Task:
        if "test_type" in kwargs:
            kwargs["test_type"] = normalize_test_type(kwargs.get("test_type"))
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
            base = base.where(Task.status == normalize_task_status(status))
        if test_type:
            base = base.where(Task.test_type == normalize_test_type(test_type))

        count_stmt = select(func.count()).select_from(base.subquery())
        total = (await db.execute(count_stmt)).scalar_one()

        offset = (page - 1) * page_size
        result = await db.execute(
            base.order_by(Task.created_at.desc()).offset(offset).limit(page_size)
        )
        return list(result.scalars()), total

    async def update_status(self, db: AsyncSession, task: Task, status: TaskStatus | str, execution_log: str | None = None) -> Task:
        normalized_status = normalize_task_status(status)
        if normalized_status is None:
            raise ValueError("status is required")
        task.status = normalized_status
        if execution_log is not None:
            task.execution_log = execution_log
        await db.commit()
        await db.refresh(task)
        return task


task_service = TaskService()

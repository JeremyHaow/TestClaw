import asyncio
import json
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.pool import NullPool

import app.worker.tasks as worker_tasks
from app.database import AsyncSessionLocal, Base, engine as api_engine
from app.models.bug_report import BugReport
from app.models.task import Task, TaskStatus, TestType as TaskTestType
from app.worker.tasks import (
    _create_worker_engine,
    _create_worker_sessionmaker,
    _worker_session_scope,
)


async def _reset_db() -> None:
    async with api_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as session:
        await session.execute(delete(BugReport))
        await session.execute(delete(Task))
        await session.commit()


def test_worker_engine_uses_null_pool_and_not_api_engine() -> None:
    worker_engine = _create_worker_engine()

    async def dispose() -> None:
        await worker_engine.dispose()

    try:
        assert isinstance(worker_engine, AsyncEngine)
        assert worker_engine is not api_engine
        assert worker_engine.sync_engine is not api_engine.sync_engine
        assert isinstance(worker_engine.sync_engine.pool, NullPool)
        assert str(worker_engine.url) == str(api_engine.url)
    finally:
        asyncio.run(dispose())


def test_worker_sessionmaker_binds_sessions_without_expiration() -> None:
    worker_engine = _create_worker_engine()

    async def inspect_session() -> None:
        try:
            session_factory = _create_worker_sessionmaker(worker_engine)
            async with session_factory() as session:
                assert isinstance(session, AsyncSession)
                assert session.sync_session.bind is worker_engine.sync_engine
                assert session.sync_session.expire_on_commit is False
        finally:
            await worker_engine.dispose()

    asyncio.run(inspect_session())


def test_worker_session_scope_survives_sequential_asyncio_run_loops() -> None:
    # Reproducing asyncpg loop ownership needs a live PostgreSQL worker stack; this checks the
    # worker-only factory contract with real sessions across consecutive asyncio.run loops.
    async def query_once() -> int:
        async with _worker_session_scope() as session:
            assert session.sync_session.bind is not api_engine.sync_engine
            assert isinstance(session.sync_session.bind.pool, NullPool)
            result = await session.execute(text("select 1"))
            return result.scalar_one()

    assert asyncio.run(query_once()) == 1
    assert asyncio.run(query_once()) == 1


def test_run_uses_worker_session_for_persistence_and_disposes_engine(monkeypatch) -> None:
    async def scenario() -> None:
        await _reset_db()
        async with AsyncSessionLocal() as session:
            task = Task(
                objective="exercise worker lifecycle",
                target_url="https://example.test",
                status=TaskStatus.QUEUED,
                test_type=TaskTestType.API,
            )
            session.add(task)
            await session.commit()
            await session.refresh(task)
            task_id = task.id

        created_engine_ids: set[int] = set()
        disposed_engine_ids: list[int] = []
        original_create_worker_engine = worker_tasks._create_worker_engine
        original_dispose = AsyncEngine.dispose

        def tracked_create_worker_engine() -> AsyncEngine:
            engine = original_create_worker_engine()
            created_engine_ids.add(id(engine))
            return engine

        async def tracked_dispose(self: AsyncEngine, close: bool = True) -> None:
            if id(self) in created_engine_ids:
                disposed_engine_ids.append(id(self))
            await original_dispose(self, close=close)

        async def fake_run_graph_with_progress(state: dict[str, Any]) -> dict[str, Any]:
            db = state["db_session"]
            assert isinstance(db, AsyncSession)
            assert db.sync_session.bind is not api_engine.sync_engine
            assert state["auth_headers"]["Authorization"] == "Bearer secret-token"
            result = await db.execute(text("select 1"))
            assert result.scalar_one() == 1
            return {
                **state,
                "api_execution_result": {
                    "completed": 1,
                    "passed": 0,
                    "failed": 1,
                    "all_passed": False,
                },
                "execution_result": {"stderr": "stack trace"},
                "bug_report": {
                    "title": "Checkout failure",
                    "root_cause": "Payment request failed",
                    "reproduce_steps": ["Open checkout", "Submit payment"],
                    "fix_suggestion": "Inspect payment gateway response handling",
                },
            }

        monkeypatch.setattr(worker_tasks, "_create_worker_engine", tracked_create_worker_engine)
        monkeypatch.setattr(AsyncEngine, "dispose", tracked_dispose)
        monkeypatch.setattr(worker_tasks, "run_graph_with_progress", fake_run_graph_with_progress)

        result = await worker_tasks._run(
            task_id,
            "exercise worker lifecycle",
            "https://example.test",
            test_type="api",
            auth_headers={"Authorization": "Bearer secret-token"},
            custom_headers={"X-Test": "true"},
        )

        assert sorted(disposed_engine_ids) == sorted(created_engine_ids)
        assert "db_session" not in result
        assert "auth_headers" not in result
        assert result["bug_report"]["title"] == "Checkout failure"

        async with AsyncSessionLocal() as session:
            persisted = await session.get(Task, task_id)
            assert persisted is not None
            assert persisted.status == TaskStatus.FAILED
            assert persisted.execution_log is not None
            execution_log = json.loads(persisted.execution_log)
            assert execution_log["api_execution_result"]["completed"] == 1
            assert execution_log["auth_headers"]["Authorization"] == "[REDACTED]"
            assert execution_log["auth_headers"]["X-Test"] == "true"

            bug_report = (
                await session.execute(select(BugReport).where(BugReport.task_id == task_id))
            ).scalar_one()
            assert bug_report.title == "Checkout failure"
            assert bug_report.reproduce_steps == "1. Open checkout\n2. Submit payment"

    asyncio.run(scenario())

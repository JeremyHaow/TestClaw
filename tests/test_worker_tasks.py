import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.pool import NullPool

from app.database import engine as api_engine
from app.worker.tasks import (
    _create_worker_engine,
    _create_worker_sessionmaker,
    _worker_session_scope,
)


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

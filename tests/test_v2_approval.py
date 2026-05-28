"""Tests for the v2 agent human-in-the-loop approval channel."""

import asyncio
import json
import uuid
from typing import Any

import pytest
from sqlalchemy import delete

from app.agent.v2.approval import (
    APPROVAL_LOG_KEY,
    ApprovalChannel,
    ApprovalRequest,
    get,
    list_pending_requests,
    pending_requests,
    register,
    remove,
    resolve_persisted_request,
)
from app.database import AsyncSessionLocal, Base, engine
from app.models.task import Task, TaskStatus, TestType as TaskTestType


def _make_request(**overrides) -> ApprovalRequest:
    """Helper to create an ApprovalRequest with sensible defaults."""
    defaults = {
        "request_id": str(uuid.uuid4()),
        "action": "POST /api/items",
        "method": "POST",
        "url": "https://example.com/api/items",
        "risk_level": "medium",
        "body_preview": '{"name": "test"}',
        "tool_call_id": "call_123",
        "tool_name": "api.http_request",
        "tool_args": {"method": "POST", "url": "https://example.com/api/items"},
    }
    defaults.update(overrides)
    return ApprovalRequest(**defaults)


# ---- ApprovalChannel basic behavior ----


@pytest.mark.asyncio
async def test_approval_channel_resolve_approve():
    """Resolving with approved=True makes request_approval return True."""
    channel = ApprovalChannel()
    request = _make_request()

    async def resolve_after_delay():
        await asyncio.sleep(0.05)
        channel.resolve(request.request_id, approved=True)

    task = asyncio.create_task(resolve_after_delay())
    result = await channel.request_approval(request)
    await task

    assert result is True
    assert request.approved is True


@pytest.mark.asyncio
async def test_approval_channel_resolve_deny():
    """Resolving with approved=False makes request_approval return False."""
    channel = ApprovalChannel()
    request = _make_request()

    async def resolve_after_delay():
        await asyncio.sleep(0.05)
        channel.resolve(request.request_id, approved=False, message="Not safe")

    task = asyncio.create_task(resolve_after_delay())
    result = await channel.request_approval(request)
    await task

    assert result is False
    assert request.approved is False
    assert request.response_message == "Not safe"


@pytest.mark.asyncio
async def test_approval_channel_timeout():
    """Unresolved request times out and returns False."""
    channel = ApprovalChannel(timeout_seconds=0.1, poll_interval_seconds=0.01)
    request = _make_request()
    result = await channel.request_approval(request)
    assert result is False
    assert request.approved is False


def test_approval_channel_get_pending():
    """get_pending returns only unresolved requests."""
    channel = ApprovalChannel()
    r1 = _make_request()
    r2 = _make_request()
    channel._requests[r1.request_id] = r1
    channel._requests[r2.request_id] = r2

    pending = channel.get_pending()
    assert len(pending) == 2

    r1.approved = True
    pending = channel.get_pending()
    assert len(pending) == 1
    assert pending[0].request_id == r2.request_id


def test_approval_channel_resolve_unknown_id():
    """Resolving an unknown request_id returns False."""
    channel = ApprovalChannel()
    assert channel.resolve("nonexistent", approved=True) is False


def test_approval_channel_has_pending():
    """has_pending reflects whether there are unresolved requests."""
    channel = ApprovalChannel()
    assert channel.has_pending is False

    r = _make_request()
    channel._requests[r.request_id] = r
    assert channel.has_pending is True

    r.approved = True
    assert channel.has_pending is False


# ---- ApprovalRequest serialization ----


def test_approval_request_to_dict():
    """to_dict produces a safe serialization without tool_args."""
    request = _make_request(body_preview='{"password": "secret-password"}')
    d = request.to_dict()

    assert d["request_id"] == request.request_id
    assert d["method"] == "POST"
    assert d["url"] == "https://example.com/api/items"
    assert d["risk_level"] == "medium"
    assert d["approved"] is None
    # tool_args should NOT be in the serialized dict (secrets).
    assert "tool_args" not in d
    assert "secret-password" not in json.dumps(d)


# ---- Module-level registry ----


def test_registry_register_and_get():
    """Register and retrieve an approval channel by task id."""
    task_id = str(uuid.uuid4())
    channel = ApprovalChannel()

    register(task_id, channel)
    assert get(task_id) is channel

    remove(task_id)
    assert get(task_id) is None


def test_registry_pending_requests_empty():
    """pending_requests returns [] for unknown task."""
    assert pending_requests("nonexistent") == []


def test_registry_pending_requests_with_data():
    """pending_requests returns serialized pending requests."""
    task_id = str(uuid.uuid4())
    channel = ApprovalChannel()
    r = _make_request()
    channel._requests[r.request_id] = r

    register(task_id, channel)
    try:
        pending = pending_requests(task_id)
        assert len(pending) == 1
        assert pending[0]["request_id"] == r.request_id
    finally:
        remove(task_id)


# ---- Persisted cross-process approval behavior ----


async def _reset_db() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as session:
        await session.execute(delete(Task))
        await session.commit()


async def _insert_task() -> str:
    task_id = str(uuid.uuid4())
    async with AsyncSessionLocal() as session:
        task = Task(
            id=task_id,
            objective="v2 approval",
            target_url="https://api.example.test",
            status=TaskStatus.RUNNING,
            test_type=TaskTestType.API,
            execution_log=json.dumps({}),
        )
        session.add(task)
        await session.commit()
    return task_id


@pytest.mark.asyncio
async def test_approval_channel_persists_and_polls_db_resolution():
    """Worker-side approval waits on DB state that API-side code can resolve."""
    await _reset_db()
    task_id = await _insert_task()

    async with AsyncSessionLocal() as worker_session:
        channel = ApprovalChannel(
            {"task_id": task_id, "db_session": worker_session},
            timeout_seconds=2,
            poll_interval_seconds=0.01,
        )
        request = _make_request(body_preview='{"password": "secret-password"}')
        approval_task = asyncio.create_task(channel.request_approval(request))

        pending: list[dict[str, Any]] = []
        for _ in range(50):
            async with AsyncSessionLocal() as api_session:
                pending = await list_pending_requests(api_session, task_id)
            if pending:
                break
            await asyncio.sleep(0.01)

        assert pending
        assert pending[0]["request_id"] == request.request_id
        assert "secret-password" not in json.dumps(pending)

        async with AsyncSessionLocal() as api_session:
            resolved = await resolve_persisted_request(
                api_session,
                task_id,
                request.request_id,
                approved=True,
                message="approved with token=secret-token",
            )

        assert resolved is True
        assert await approval_task is True
        assert request.approved is True

    async with AsyncSessionLocal() as session:
        task = await session.get(Task, task_id)
        assert task is not None
        log = json.loads(task.execution_log or "{}")
    records = log[APPROVAL_LOG_KEY]
    assert records[0]["status"] == "approved"
    assert "secret-token" not in json.dumps(records)

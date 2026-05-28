"""Human-in-the-loop approval channel for v2 agent architecture.

Approval requests are persisted into ``Task.execution_log`` so the FastAPI
process can list/resolve requests created by a Celery worker process.  The
in-process future registry is kept as an optimization for tests and local
synchronous execution, but it is not the source of truth.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.progress import parse_execution_log, utc_now_iso
from app.core.redaction import redact_sensitive_data
from app.models.task import Task

logger = logging.getLogger(__name__)

APPROVAL_LOG_KEY = "v2_approval_requests"
APPROVAL_PENDING = "pending"
APPROVAL_APPROVED = "approved"
APPROVAL_DENIED = "denied"
APPROVAL_TIMED_OUT = "timed_out"
_APPROVAL_TIMEOUT_SECONDS = 300.0
_APPROVAL_POLL_INTERVAL_SECONDS = 1.0


def _default_approval_timeout_seconds() -> float:
    from app.config import settings

    return max(
        1.0,
        float(getattr(settings, "AGENT_V2_APPROVAL_TIMEOUT_SECONDS", _APPROVAL_TIMEOUT_SECONDS)),
    )


def _default_poll_interval_seconds() -> float:
    from app.config import settings

    return max(
        0.1,
        float(
            getattr(
                settings,
                "AGENT_V2_APPROVAL_POLL_INTERVAL_SECONDS",
                _APPROVAL_POLL_INTERVAL_SECONDS,
            )
        ),
    )


@dataclass
class ApprovalRequest:
    """A single approval request pending user decision."""

    request_id: str
    action: str
    method: str
    url: str
    risk_level: str
    body_preview: str
    tool_call_id: str
    tool_name: str
    tool_args: dict[str, Any]
    approved: bool | None = None
    response_message: str | None = None
    requested_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for SSE events and API responses without raw tool args."""
        return redact_sensitive_data(
            {
                "request_id": self.request_id,
                "action": self.action,
                "method": self.method,
                "url": self.url,
                "risk_level": self.risk_level,
                "body_preview": self.body_preview,
                "tool_name": self.tool_name,
                "approved": self.approved,
                "response_message": self.response_message,
                "requested_at": self.requested_at,
            }
        )

    def to_record(self, *, status: str = APPROVAL_PENDING) -> dict[str, Any]:
        record = self.to_dict()
        record["status"] = status
        return record


def _approval_records(parsed_log: dict[str, Any]) -> list[dict[str, Any]]:
    records = parsed_log.get(APPROVAL_LOG_KEY)
    return [item for item in records if isinstance(item, dict)] if isinstance(records, list) else []


async def _load_task(db: AsyncSession, task_id: str) -> Task | None:
    task = await db.get(Task, task_id)
    if task is not None:
        await db.refresh(task)
    return task


async def _persist_records(
    db: AsyncSession,
    task: Task,
    records: list[dict[str, Any]],
) -> None:
    parsed = parse_execution_log(task.execution_log)
    parsed[APPROVAL_LOG_KEY] = redact_sensitive_data(records)
    task.execution_log = json.dumps(redact_sensitive_data(parsed), ensure_ascii=False, default=str)
    await db.commit()


async def list_pending_requests(db: AsyncSession, task_id: str) -> list[dict[str, Any]]:
    """Return pending approval requests persisted for a run."""
    task = await _load_task(db, task_id)
    if task is None:
        return []
    records = _approval_records(parse_execution_log(task.execution_log))
    return [
        redact_sensitive_data(record)
        for record in records
        if record.get("status") == APPROVAL_PENDING
    ]


async def resolve_persisted_request(
    db: AsyncSession,
    task_id: str,
    request_id: str,
    approved: bool,
    message: str | None = None,
) -> bool:
    """Resolve an approval request persisted in ``Task.execution_log``."""
    task = await _load_task(db, task_id)
    if task is None:
        return False

    parsed = parse_execution_log(task.execution_log)
    records = _approval_records(parsed)
    found = False
    for record in records:
        if record.get("request_id") != request_id:
            continue
        if record.get("status") != APPROVAL_PENDING:
            return False
        record["approved"] = bool(approved)
        record["status"] = APPROVAL_APPROVED if approved else APPROVAL_DENIED
        record["response_message"] = redact_sensitive_data(message)
        record["resolved_at"] = utc_now_iso()
        found = True
        break

    if not found:
        return False

    await _persist_records(db, task, records)

    channel = get(task_id)
    if channel:
        channel.resolve(request_id, approved, message)
    return True


class ApprovalChannel:
    """Async channel for human-in-the-loop approval.

    ``state`` may contain ``task_id`` and ``db_session``.  When present, approval
    requests are persisted and polled from the database so separate API/worker
    processes can coordinate.
    """

    def __init__(
        self,
        state: dict[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
        poll_interval_seconds: float | None = None,
    ) -> None:
        self.state = state if state is not None else {}
        self.timeout_seconds = (
            _default_approval_timeout_seconds()
            if timeout_seconds is None
            else max(1.0, float(timeout_seconds))
        )
        self.poll_interval_seconds = (
            _default_poll_interval_seconds()
            if poll_interval_seconds is None
            else max(0.1, float(poll_interval_seconds))
        )
        self._pending: dict[str, asyncio.Future[bool]] = {}
        self._requests: dict[str, ApprovalRequest] = {}

    async def request_approval(self, request: ApprovalRequest) -> bool:
        """Submit an approval request and wait for approve/deny/timeout."""
        future: asyncio.Future[bool] = asyncio.get_event_loop().create_future()
        self._pending[request.request_id] = future
        self._requests[request.request_id] = request
        await self._persist_request(request)

        logger.info(
            "Approval request submitted: %s - %s %s",
            request.request_id,
            request.method,
            request.url,
        )

        deadline = asyncio.get_event_loop().time() + self.timeout_seconds
        try:
            while True:
                if future.done():
                    return bool(future.result())

                persisted = await self._load_persisted_request(request.request_id)
                if persisted:
                    status = str(persisted.get("status") or "")
                    if status in {APPROVAL_APPROVED, APPROVAL_DENIED}:
                        approved = status == APPROVAL_APPROVED
                        request.approved = approved
                        request.response_message = persisted.get("response_message")
                        if not future.done():
                            future.set_result(approved)
                        return approved

                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    request.approved = False
                    request.response_message = "timed out"
                    await self._mark_timeout(request.request_id)
                    return False
                try:
                    approved = await asyncio.wait_for(
                        asyncio.shield(future),
                        timeout=min(self.poll_interval_seconds, remaining),
                    )
                    return bool(approved)
                except asyncio.TimeoutError:
                    continue
        finally:
            self._pending.pop(request.request_id, None)

    def resolve(
        self, request_id: str, approved: bool, message: str | None = None
    ) -> bool:
        """Resolve an in-process approval request."""
        request = self._requests.get(request_id)
        if not request:
            return False

        request.approved = approved
        request.response_message = message

        future = self._pending.get(request_id)
        if future and not future.done():
            future.set_result(approved)
            return True
        return False

    def get_pending(self) -> list[ApprovalRequest]:
        """Get all pending in-process approval requests."""
        return [request for request in self._requests.values() if request.approved is None]

    def get_request(self, request_id: str) -> ApprovalRequest | None:
        """Get a specific in-process approval request by id."""
        return self._requests.get(request_id)

    @property
    def has_pending(self) -> bool:
        """Return ``True`` if there are unresolved in-process requests."""
        return any(request.approved is None for request in self._requests.values())

    async def _persist_request(self, request: ApprovalRequest) -> None:
        db = self.state.get("db_session")
        task_id = str(self.state.get("task_id") or "")
        if not db or not task_id:
            self.state.setdefault(APPROVAL_LOG_KEY, []).append(request.to_record())
            return

        task = await _load_task(db, task_id)
        if task is None:
            return
        parsed = parse_execution_log(task.execution_log)
        records = _approval_records(parsed)
        records = [record for record in records if record.get("request_id") != request.request_id]
        records.append(request.to_record())
        self.state[APPROVAL_LOG_KEY] = records
        await _persist_records(db, task, records)

    async def _load_persisted_request(self, request_id: str) -> dict[str, Any] | None:
        db = self.state.get("db_session")
        task_id = str(self.state.get("task_id") or "")
        if not db or not task_id:
            return None

        task = await _load_task(db, task_id)
        if task is None:
            return None
        records = _approval_records(parse_execution_log(task.execution_log))
        self.state[APPROVAL_LOG_KEY] = records
        for record in records:
            if record.get("request_id") == request_id:
                return record
        return None

    async def _mark_timeout(self, request_id: str) -> None:
        db = self.state.get("db_session")
        task_id = str(self.state.get("task_id") or "")
        if not db or not task_id:
            return

        task = await _load_task(db, task_id)
        if task is None:
            return
        parsed = parse_execution_log(task.execution_log)
        records = _approval_records(parsed)
        for record in records:
            if record.get("request_id") == request_id and record.get("status") == APPROVAL_PENDING:
                record["status"] = APPROVAL_TIMED_OUT
                record["approved"] = False
                record["response_message"] = "timed out"
                record["resolved_at"] = utc_now_iso()
                self.state[APPROVAL_LOG_KEY] = records
                await _persist_records(db, task, records)
                return


_registry: dict[str, ApprovalChannel] = {}


def register(task_id: str, channel: ApprovalChannel) -> None:
    """Register an in-process approval channel for a task."""
    _registry[task_id] = channel
    logger.info("Approval channel registered for task %s", task_id)


def get(task_id: str) -> ApprovalChannel | None:
    """Look up the in-process approval channel for a task."""
    return _registry.get(task_id)


def remove(task_id: str) -> None:
    """Remove the in-process approval channel for a task."""
    _registry.pop(task_id, None)


def pending_requests(task_id: str) -> list[dict[str, Any]]:
    """Return serialized in-process pending requests, or empty list."""
    channel = _registry.get(task_id)
    if not channel:
        return []
    return [request.to_dict() for request in channel.get_pending()]

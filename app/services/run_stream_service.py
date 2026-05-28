import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

from sqlalchemy import func, select

from app.core.redaction import redact_json_text, redact_sensitive_data
from app.database import AsyncSessionLocal
from app.models.run_event import RunEvent
from app.services.task_service import task_service

logger = logging.getLogger(__name__)

TERMINAL_RUN_STATUSES = {"succeeded", "failed", "bug_found", "cancelled"}

RunTriageSummaryBuilder = Callable[[str, dict[str, Any]], dict[str, Any]]
RunInterventionSummaryBuilder = Callable[
    [str, dict[str, Any], dict[str, Any]],
    dict[str, Any],
]


def _status_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value or "")


def _sse_message(payload: dict[str, Any], event_name: str | None = None) -> str:
    safe_payload = redact_sensitive_data(payload)
    data = json.dumps(safe_payload, ensure_ascii=False, default=str)
    if event_name:
        return f"event: {event_name}\ndata: {data}\n\n"
    return f"data: {data}\n\n"


def _event_title(event_type: str) -> str:
    return {
        "run.status": "Run status changed",
        "run.snapshot": "Run snapshot updated",
        "run.workflow": "Run workflow updated",
        "run.log": "Run log updated",
        "run.finished": "Run finished",
    }.get(event_type, event_type)


def _event_summary(payload: dict[str, Any]) -> str | None:
    payload_type = str(payload.get("type") or "")
    status = payload.get("status")
    if payload_type in {"status", "done"} and status:
        return f"Run status: {status}"
    if payload_type == "workflow":
        steps = payload.get("steps")
        if isinstance(steps, list):
            return f"Workflow steps: {len(steps)}"
    if payload_type == "log":
        return "Execution log changed"
    if payload_type == "snapshot":
        return "Execution snapshot changed"
    return None


async def _next_run_event_sequence(run_id: str) -> int:
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(func.max(RunEvent.sequence)).where(RunEvent.run_id == run_id)
            )
            return int(result.scalar_one_or_none() or 0) + 1
    except Exception as exc:
        logger.warning("Failed to load run stream event sequence for run %s: %s", run_id, exc)
        return 1


async def _persist_run_event(
    *,
    run_id: str,
    sequence: int,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    try:
        async with AsyncSessionLocal() as db:
            safe_payload = redact_sensitive_data(payload)
            db.add(
                RunEvent(
                    run_id=run_id,
                    sequence=sequence,
                    event_type=event_type,
                    title=_event_title(event_type),
                    summary=_event_summary(safe_payload),
                    payload_json=safe_payload if isinstance(safe_payload, dict) else {},
                )
            )
            await db.commit()
    except Exception as exc:
        logger.warning("Failed to persist run stream event for run %s: %s", run_id, exc)


async def _emit(
    *,
    run_id: str,
    sequence: int,
    event_type: str,
    payload: dict[str, Any],
    event_name: str | None = None,
) -> str:
    await _persist_run_event(
        run_id=run_id,
        sequence=sequence,
        event_type=event_type,
        payload=payload,
    )
    message = _sse_message(payload, event_name=event_name)
    if event_name:
        message += _sse_message(payload)
    return message


async def stream_run_events(
    run_id: str,
    *,
    build_triage_summary: RunTriageSummaryBuilder,
    build_intervention_summary: RunInterventionSummaryBuilder,
    poll_interval_seconds: float = 2.0,
) -> AsyncIterator[str]:
    """Yield redacted SSE messages for run progress."""
    last_status: str | None = None
    last_log = ""
    sequence = await _next_run_event_sequence(run_id)

    while True:
        async with AsyncSessionLocal() as stream_db:
            task = await task_service.get(stream_db, run_id)
            if task is None:
                yield _sse_message({"error": "Run not found"})
                break
            current_status = _status_value(task.status)
            current_log = task.execution_log or ""

        if current_status != last_status:
            payload = {"run_id": run_id, "type": "status", "status": current_status}
            yield await _emit(
                run_id=run_id,
                sequence=sequence,
                event_type="run.status",
                payload=payload,
                event_name="run.status",
            )
            sequence += 1
            last_status = current_status

        if current_log != last_log:
            try:
                log_data = json.loads(current_log)
                if not isinstance(log_data, dict):
                    log_data = {"raw_log": log_data}
                try:
                    from app.agent.runtime.event_store import load_runtime_detail

                    async with AsyncSessionLocal() as runtime_db:
                        runtime_detail = await load_runtime_detail(runtime_db, run_id)
                    if runtime_detail:
                        log_data.update({key: value for key, value in runtime_detail.items() if value})
                except Exception as exc:
                    logger.debug("Unable to load runtime detail for stream: %s", exc)
                log_data = redact_sensitive_data(log_data)
                triage_summary = build_triage_summary(current_status, log_data)
                log_data["triage_summary"] = triage_summary
                log_data["intervention_summary"] = build_intervention_summary(
                    current_status,
                    log_data,
                    triage_summary,
                )
                snapshot_payload = {
                    "run_id": run_id,
                    "type": "snapshot",
                    "snapshot": log_data,
                }
                yield await _emit(
                    run_id=run_id,
                    sequence=sequence,
                    event_type="run.snapshot",
                    payload=snapshot_payload,
                )
                sequence += 1

                steps = log_data.get("workflow_steps") or []
                if steps:
                    workflow_payload = {"run_id": run_id, "type": "workflow", "steps": steps}
                    yield await _emit(
                        run_id=run_id,
                        sequence=sequence,
                        event_type="run.workflow",
                        payload=workflow_payload,
                    )
                    sequence += 1
            except Exception as exc:
                logger.debug("Unable to parse run execution log for stream: %s", exc)

            safe_log = redact_json_text(current_log) or ""
            log_payload = {"run_id": run_id, "type": "log", "log": safe_log[:2000]}
            yield await _emit(
                run_id=run_id,
                sequence=sequence,
                event_type="run.log",
                payload=log_payload,
            )
            sequence += 1
            last_log = current_log

        if current_status in TERMINAL_RUN_STATUSES:
            done_payload = {"run_id": run_id, "type": "done", "status": current_status}
            yield await _emit(
                run_id=run_id,
                sequence=sequence,
                event_type="run.finished",
                payload=done_payload,
                event_name="run.finished",
            )
            break

        await asyncio.sleep(poll_interval_seconds)

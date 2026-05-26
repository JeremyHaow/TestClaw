from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Awaitable, Callable

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from app.core.dependencies import CurrentUser, DbSession
from app.core.redaction import redact_sensitive_text
from app.models.agent_planning import AgentPlanningMessage, AgentPlanningSession
from app.models.task import Task
from app.schemas.task import parse_task_detail
from app.services.agent_planning import (
    PLAN_SESSION_EXECUTED,
    PLAN_SESSION_READY,
    agent_planning_service,
    parse_json_object_text,
    redacted_plan_session_payload,
)

router = APIRouter()
logger = logging.getLogger(__name__)

PLANNER_PROCESS_STEPS: tuple[dict[str, str], ...] = (
    {"code": "analyzing_requirement", "label": "正在分析需求"},
    {"code": "checking_missing_info", "label": "正在检查缺失信息"},
    {"code": "normalizing_target", "label": "正在归一化目标"},
    {"code": "preparing_plan", "label": "正在准备计划"},
)
WAITING_PROCESS_STEP = {"code": "waiting_for_confirmation", "label": "等待确认"}


class AgentPlanCreateRequest(BaseModel):
    title: str | None = None
    initial_message: str | None = None


class AgentPlanMessageRequest(BaseModel):
    content: str = Field(min_length=1)


class AgentPlanMessageEditRequest(BaseModel):
    content: str = Field(min_length=1)


class AgentPlanRejectRequest(BaseModel):
    reason: str | None = None


class AgentPlanExecuteResponse(BaseModel):
    session: dict[str, Any]
    run: dict[str, Any]


async def _load_owned_session(session_id: str, db: DbSession, user: CurrentUser):
    session = await agent_planning_service.get_session(
        db,
        session_id=session_id,
        user_id=user.id,
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Planning session not found")
    return session


def _ensure_session_mutable(session: AgentPlanningSession) -> None:
    if session.status == PLAN_SESSION_EXECUTED:
        raise HTTPException(status_code=400, detail="Executed plan cannot be changed")


async def _ensure_editable_user_message(
    session: AgentPlanningSession,
    message_id: str,
    db: DbSession,
) -> None:
    messages = await agent_planning_service.list_messages(db, session_id=session.id)
    for message in messages:
        if message.id != message_id:
            continue
        if message.role != "user":
            raise HTTPException(status_code=400, detail="Only user messages can be edited")
        return
    raise HTTPException(status_code=404, detail="Planning message not found")


async def _execute_run_payload(payload: dict[str, Any], db: DbSession, user: CurrentUser) -> Task:
    from app.api.v1.runs import RunCreate, create_run

    return await create_run(RunCreate(**payload), db, user)


def _sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _text_chunks(text: str, *, size: int = 18) -> list[str]:
    if not text:
        return []
    return [text[index : index + size] for index in range(0, len(text), size)]


def _latest_assistant_text(messages: list[AgentPlanningMessage]) -> str:
    for message in reversed(messages):
        if message.role == "assistant":
            return str(message.content or "")
    return ""


async def _stream_turn_events(
    action: Callable[[], Awaitable[tuple[AgentPlanningSession, list[AgentPlanningMessage]]]],
) -> AsyncIterator[str]:
    process_events: list[dict[str, str]] = []
    for step in PLANNER_PROCESS_STEPS:
        event = {**step, "status": "done"}
        process_events.append(event)
        yield _sse_event("process", event)
        await asyncio.sleep(0)

    try:
        session, messages = await action()
        assistant_text = _latest_assistant_text(messages)
        for chunk in _text_chunks(assistant_text):
            yield _sse_event("token", {"delta": chunk})
            await asyncio.sleep(0)
        if session.status == PLAN_SESSION_READY:
            waiting_event = {**WAITING_PROCESS_STEP, "status": "active"}
            process_events.append(waiting_event)
            yield _sse_event("process", waiting_event)
        yield _sse_event(
            "final",
            {
                "session": redacted_plan_session_payload(session, messages),
                "process_events": process_events,
            },
        )
    except Exception as exc:
        logger.exception("Planner stream failed")
        detail = redact_sensitive_text(str(exc) or "Planner stream failed")
        yield _sse_event("error", {"detail": detail})


@router.post("")
async def create_planning_session(
    payload: AgentPlanCreateRequest,
    db: DbSession,
    user: CurrentUser,
):
    session = await agent_planning_service.create_session(
        db,
        user_id=user.id,
        title=payload.title,
    )
    messages = []
    if payload.initial_message and payload.initial_message.strip():
        session, messages = await agent_planning_service.add_user_message(
            db,
            session=session,
            content=payload.initial_message,
        )
    return redacted_plan_session_payload(session, messages)


@router.get("")
async def list_planning_sessions(
    db: DbSession,
    user: CurrentUser,
    limit: int = Query(default=30, ge=1, le=100),
):
    sessions = await agent_planning_service.list_sessions(db, user_id=user.id, limit=limit)
    return [redacted_plan_session_payload(session) for session in sessions]


@router.get("/{session_id}")
async def get_planning_session(session_id: str, db: DbSession, user: CurrentUser):
    session = await _load_owned_session(session_id, db, user)
    messages = await agent_planning_service.list_messages(db, session_id=session.id)
    return redacted_plan_session_payload(session, messages)


@router.delete("/{session_id}", status_code=204)
async def delete_planning_session(session_id: str, db: DbSession, user: CurrentUser):
    session = await _load_owned_session(session_id, db, user)
    await agent_planning_service.delete_session(db, session=session)
    return Response(status_code=204)


@router.post("/{session_id}/messages")
async def add_planning_message(
    session_id: str,
    payload: AgentPlanMessageRequest,
    db: DbSession,
    user: CurrentUser,
):
    session = await _load_owned_session(session_id, db, user)
    _ensure_session_mutable(session)
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    session, messages = await agent_planning_service.add_user_message(
        db,
        session=session,
        content=content,
    )
    return redacted_plan_session_payload(session, messages)


@router.post("/{session_id}/messages/stream")
async def stream_planning_message(
    session_id: str,
    payload: AgentPlanMessageRequest,
    db: DbSession,
    user: CurrentUser,
):
    session = await _load_owned_session(session_id, db, user)
    _ensure_session_mutable(session)
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")

    async def action() -> tuple[AgentPlanningSession, list[AgentPlanningMessage]]:
        return await agent_planning_service.add_user_message(
            db,
            session=session,
            content=content,
        )

    return StreamingResponse(_stream_turn_events(action), media_type="text/event-stream")


@router.put("/{session_id}/messages/{message_id}")
async def edit_planning_message(
    session_id: str,
    message_id: str,
    payload: AgentPlanMessageEditRequest,
    db: DbSession,
    user: CurrentUser,
):
    session = await _load_owned_session(session_id, db, user)
    _ensure_session_mutable(session)
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    try:
        session, messages = await agent_planning_service.edit_user_message(
            db,
            session=session,
            message_id=message_id,
            content=content,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Planning message not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return redacted_plan_session_payload(session, messages)


@router.put("/{session_id}/messages/{message_id}/stream")
async def stream_edit_planning_message(
    session_id: str,
    message_id: str,
    payload: AgentPlanMessageEditRequest,
    db: DbSession,
    user: CurrentUser,
):
    session = await _load_owned_session(session_id, db, user)
    _ensure_session_mutable(session)
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    await _ensure_editable_user_message(session, message_id, db)

    async def action() -> tuple[AgentPlanningSession, list[AgentPlanningMessage]]:
        return await agent_planning_service.edit_user_message(
            db,
            session=session,
            message_id=message_id,
            content=content,
        )

    return StreamingResponse(_stream_turn_events(action), media_type="text/event-stream")


@router.delete("/{session_id}/messages/{message_id}")
async def delete_planning_message(
    session_id: str,
    message_id: str,
    db: DbSession,
    user: CurrentUser,
):
    session = await _load_owned_session(session_id, db, user)
    _ensure_session_mutable(session)
    try:
        session, messages = await agent_planning_service.delete_messages_from(
            db,
            session=session,
            message_id=message_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Planning message not found") from exc
    return redacted_plan_session_payload(session, messages)


@router.post("/{session_id}/reject")
async def reject_current_plan(
    session_id: str,
    payload: AgentPlanRejectRequest,
    db: DbSession,
    user: CurrentUser,
):
    session = await _load_owned_session(session_id, db, user)
    if session.status == PLAN_SESSION_EXECUTED:
        raise HTTPException(status_code=400, detail="Executed plan cannot be rejected")
    if not session.current_plan:
        raise HTTPException(status_code=400, detail="No current plan to reject")
    session, messages = await agent_planning_service.reject_plan(
        db,
        session=session,
        reason=payload.reason,
    )
    return redacted_plan_session_payload(session, messages)


@router.post("/{session_id}/execute", response_model=AgentPlanExecuteResponse)
async def execute_current_plan(
    session_id: str,
    db: DbSession,
    user: CurrentUser,
):
    session = await _load_owned_session(session_id, db, user)
    run_payload = parse_json_object_text(session.current_run_payload)
    if not run_payload:
        raise HTTPException(status_code=400, detail="No executable plan is ready")
    try:
        task = await _execute_run_payload(run_payload, db, user)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session.status = PLAN_SESSION_EXECUTED
    session.executed_run_id = task.id
    await db.commit()
    await db.refresh(session)
    messages = await agent_planning_service.list_messages(db, session_id=session.id)
    run_detail = parse_task_detail(task)
    run_detail.pop("execution_log", None)
    return {
        "session": redacted_plan_session_payload(session, messages),
        "run": json.loads(json.dumps(run_detail, ensure_ascii=False, default=str)),
    }

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.dependencies import CurrentUser, DbSession
from app.models.task import Task
from app.schemas.task import parse_task_detail
from app.services.agent_planning import (
    PLAN_SESSION_EXECUTED,
    agent_planning_service,
    parse_json_object_text,
    redacted_plan_session_payload,
)

router = APIRouter()


class AgentPlanCreateRequest(BaseModel):
    title: str | None = None
    initial_message: str | None = None


class AgentPlanMessageRequest(BaseModel):
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


async def _execute_run_payload(payload: dict[str, Any], db: DbSession, user: CurrentUser) -> Task:
    from app.api.v1.runs import RunCreate, create_run

    return await create_run(RunCreate(**payload), db, user)


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


@router.post("/{session_id}/messages")
async def add_planning_message(
    session_id: str,
    payload: AgentPlanMessageRequest,
    db: DbSession,
    user: CurrentUser,
):
    session = await _load_owned_session(session_id, db, user)
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    session, messages = await agent_planning_service.add_user_message(
        db,
        session=session,
        content=content,
    )
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

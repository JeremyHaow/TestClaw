from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Awaitable, Callable

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from app.core.dependencies import CurrentUser, DbSession
from app.core.redaction import redact_sensitive_data, redact_sensitive_text
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


class AgentPlanIntakeRequest(BaseModel):
    message: str | None = None
    selected_option: Any | None = None
    current_step: str | None = None


class AgentPlanRejectRequest(BaseModel):
    reason: str | None = None


class AgentPlanExecuteResponse(BaseModel):
    session: dict[str, Any]
    run: dict[str, Any]


class AgentPlanCreateRunResponse(BaseModel):
    run_id: str
    detail_url: str


INTAKE_STEP_ALIASES = {
    "target": "target",
    "target_kind": "target",
    "target_type": "target",
    "source": "target",
    "scope": "scope",
    "coverage": "scope",
    "coverage_scope": "scope",
    "auth": "auth",
    "login": "auth",
    "credentials": "auth",
    "auth_boundary": "auth",
    "safety": "safety",
    "policy": "safety",
    "safety_boundary": "safety",
    "success": "success",
    "criteria": "success",
    "success_criteria": "success",
}
INTAKE_FIELD_LABELS = {
    "target": "测试目标",
    "scope": "覆盖范围",
    "auth": "登录方式",
    "safety": "安全边界",
    "success": "成功标准",
}


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


def _agent_plan_current_step(session: AgentPlanningSession) -> str:
    if session.status == PLAN_SESSION_EXECUTED:
        return "executed"
    if session.status == PLAN_SESSION_READY or session.current_run_payload:
        return "review"
    return "target"


def _agent_plan_session_alias_payload(
    session: AgentPlanningSession,
    messages: list[AgentPlanningMessage] | None = None,
) -> dict[str, Any]:
    payload = redacted_plan_session_payload(session, messages)
    payload["current_step"] = _agent_plan_current_step(session)
    return payload


def _intake_step_key(value: Any) -> str | None:
    normalized = str(value or "").strip().lower()
    return INTAKE_STEP_ALIASES.get(normalized)


def _intake_text(value: Any, *, limit: int = 240) -> str | None:
    safe_value = redact_sensitive_data(value)
    if safe_value is None:
        return None
    if isinstance(safe_value, str):
        text = " ".join(safe_value.split())
    elif isinstance(safe_value, list):
        items = [
            item
            for item in (_intake_text(entry, limit=80) for entry in safe_value[:5])
            if item
        ]
        text = "、".join(items)
    elif isinstance(safe_value, dict):
        text = json.dumps(safe_value, ensure_ascii=False, default=str)
    else:
        text = str(safe_value)
    text = redact_sensitive_text(text).strip()
    return text[:limit] if text else None


def _selected_option_summary(
    selected_option: Any,
    current_step: str | None = None,
) -> str | None:
    if selected_option is None:
        return None
    safe_option = redact_sensitive_data(selected_option)
    step_key = _intake_step_key(current_step)
    if isinstance(safe_option, dict):
        message = _intake_text(safe_option.get("message") or safe_option.get("summary"))
        if message:
            return message
        label = _intake_text(safe_option.get("title") or safe_option.get("label"), limit=80)
        value = _intake_text(safe_option.get("value"), limit=80)
        pieces = [piece for piece in (label, value) if piece]
        if pieces:
            prefix = INTAKE_FIELD_LABELS.get(step_key or "", "已选择")
            return f"{prefix}：{' / '.join(pieces)}"
    text = _intake_text(safe_option)
    if not text:
        return None
    prefix = INTAKE_FIELD_LABELS.get(step_key or "")
    return f"{prefix}：{text}" if prefix else text


def _intake_message_content(payload: AgentPlanIntakeRequest) -> str:
    parts: list[str] = []
    option_summary = _selected_option_summary(payload.selected_option, payload.current_step)
    message = " ".join(str(payload.message or "").split())
    if option_summary:
        parts.append(option_summary)
    if message:
        parts.append(message)
    return "\n".join(parts).strip()


def _question_group_step(group: dict[str, Any]) -> str | None:
    step = _intake_step_key(group.get("step"))
    if step:
        return step
    options = group.get("options")
    if isinstance(options, list) and options:
        first_option = options[0]
        if isinstance(first_option, dict):
            return _intake_step_key(first_option.get("step") or first_option.get("field"))
    return None


def _latest_question_groups(session_payload: dict[str, Any]) -> list[dict[str, Any]]:
    groups = session_payload.get("question_options")
    if not isinstance(groups, list):
        return []
    return [group for group in groups if isinstance(group, dict)]


def _agent_plan_next_question(groups: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not groups:
        return None
    group = groups[0]
    options = group.get("options") if isinstance(group.get("options"), list) else []
    return redact_sensitive_data(
        {
            "step": _question_group_step(group),
            "title": _intake_text(group.get("question"), limit=180),
            "options": options,
        }
    )


def _draft_item(value: Any, *, missing: bool = False) -> dict[str, Any]:
    text = _intake_text(value)
    if text:
        return {"value": text, "status": "confirmed"}
    return {"value": None, "status": "missing" if missing else "pending"}


def _agent_plan_draft(
    session_payload: dict[str, Any],
    groups: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    current_plan = session_payload.get("current_plan")
    if not isinstance(current_plan, dict):
        current_plan = {}
    current_payload = session_payload.get("current_run_payload")
    if not isinstance(current_payload, dict):
        current_payload = {}
    missing_steps = {
        step
        for group in groups
        if group.get("required", True)
        for step in [_question_group_step(group)]
        if step
    }

    return {
        "target": _draft_item(
            current_plan.get("target")
            or current_payload.get("source")
            or current_payload.get("base_url"),
            missing="target" in missing_steps,
        ),
        "scope": _draft_item(current_plan.get("scope"), missing="scope" in missing_steps),
        "auth": _draft_item(
            current_plan.get("auth_summary") or current_payload.get("auth_mode"),
            missing="auth" in missing_steps,
        ),
        "safety": _draft_item(
            current_plan.get("safety") or current_payload.get("api_execution_policy"),
            missing="safety" in missing_steps,
        ),
        "success": _draft_item(
            current_plan.get("summary") or current_payload.get("objective"),
            missing="success" in missing_steps,
        ),
    }


def _agent_plan_extracted(session_payload: dict[str, Any]) -> dict[str, Any]:
    current_plan = session_payload.get("current_plan")
    if not isinstance(current_plan, dict):
        current_plan = {}
    current_payload = session_payload.get("current_run_payload")
    if not isinstance(current_payload, dict):
        current_payload = {}
    return redact_sensitive_data(
        {
            "target": current_plan.get("target")
            or current_payload.get("source")
            or current_payload.get("base_url"),
            "scope": current_plan.get("scope") or current_payload.get("objective"),
            "safety": current_plan.get("safety") or current_payload.get("api_execution_policy"),
            "test_type": current_plan.get("test_type") or current_payload.get("test_type"),
        }
    )


def _agent_plan_missing_info(
    draft: dict[str, dict[str, Any]],
    groups: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    missing: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        step = _question_group_step(group)
        if not step or step in seen:
            continue
        seen.add(step)
        missing.append(
            {
                "key": step,
                "label": INTAKE_FIELD_LABELS.get(step, step),
                "required": bool(group.get("required", True)),
            }
        )
    if missing:
        return missing
    for key, item in draft.items():
        if item.get("status") == "missing":
            missing.append(
                {
                    "key": key,
                    "label": INTAKE_FIELD_LABELS.get(key, key),
                    "required": True,
                }
            )
    return missing


def _agent_plan_intake_payload(
    session: AgentPlanningSession,
    messages: list[AgentPlanningMessage],
) -> dict[str, Any]:
    session_payload = _agent_plan_session_alias_payload(session, messages)
    question_groups = _latest_question_groups(session_payload)
    draft = _agent_plan_draft(session_payload, question_groups)
    return {
        "extracted": _agent_plan_extracted(session_payload),
        "draft": redact_sensitive_data(draft),
        "next_question": _agent_plan_next_question(question_groups),
        "missing_info": redact_sensitive_data(
            _agent_plan_missing_info(draft, question_groups)
        ),
        "session": session_payload,
    }


def _agent_plan_generate_summary(current_plan: dict[str, Any]) -> str:
    for key in ("summary", "title", "objective"):
        summary = _intake_text(current_plan.get(key), limit=500)
        if summary:
            return summary
    return "测试智能体计划已准备好。"


def _agent_plan_safety_boundary(
    current_plan: dict[str, Any],
    current_run_payload: dict[str, Any],
) -> dict[str, Any]:
    safety = current_plan.get("safety")
    policy = current_run_payload.get("api_execution_policy")
    return redact_sensitive_data(
        {
            "api_execution_policy": policy or "safe_read_only",
            "write_allowed": policy == "write_allowed",
            "constraints": safety if isinstance(safety, list) else ([safety] if safety else []),
        }
    )


def _agent_plan_generate_payload(
    session: AgentPlanningSession,
    messages: list[AgentPlanningMessage],
) -> dict[str, Any]:
    current_plan = parse_json_object_text(session.current_plan)
    current_run_payload = parse_json_object_text(session.current_run_payload)
    if not current_plan or not current_run_payload:
        raise HTTPException(status_code=400, detail="No executable plan is ready")

    test_type = str(
        current_run_payload.get("test_type") or current_plan.get("test_type") or ""
    ).lower()
    redacted_plan = redact_sensitive_data(current_plan)
    redacted_run_payload = redact_sensitive_data(current_run_payload)
    api_plan = redacted_plan if test_type == "api" else {}
    ui_plan = redacted_plan if test_type == "ui" else {}

    return {
        "plan_id": session.id,
        "status": "ready",
        "summary": _agent_plan_generate_summary(current_plan),
        "api_plan": api_plan,
        "ui_plan": ui_plan,
        "safety_boundary": _agent_plan_safety_boundary(current_plan, current_run_payload),
        "recommended_run_payload": redacted_run_payload,
        "session": _agent_plan_session_alias_payload(session, messages),
    }


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


def _agent_plan_run_link(run_id: str) -> dict[str, str]:
    return {"run_id": run_id, "detail_url": f"/runs/{run_id}"}


async def _load_executed_plan_task(session: AgentPlanningSession, db: DbSession) -> Task:
    if not session.executed_run_id:
        raise HTTPException(status_code=400, detail="Plan has already been executed")
    task = await db.get(Task, session.executed_run_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Executed run not found")
    return task


async def _create_run_from_plan_session(
    session: AgentPlanningSession,
    db: DbSession,
    user: CurrentUser,
) -> Task:
    if session.status == PLAN_SESSION_EXECUTED:
        return await _load_executed_plan_task(session, db)

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
    return task


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


@router.post("/sessions")
async def create_agent_plan_session_alias(
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
    return _agent_plan_session_alias_payload(session, messages)


@router.get("/sessions")
async def list_agent_plan_session_aliases(
    db: DbSession,
    user: CurrentUser,
    limit: int = Query(default=30, ge=1, le=100),
):
    sessions = await agent_planning_service.list_sessions(db, user_id=user.id, limit=limit)
    return [_agent_plan_session_alias_payload(session) for session in sessions]


@router.post("/sessions/{session_id}/intake")
async def intake_agent_plan_session_alias(
    session_id: str,
    payload: AgentPlanIntakeRequest,
    db: DbSession,
    user: CurrentUser,
):
    session = await _load_owned_session(session_id, db, user)
    _ensure_session_mutable(session)
    content = _intake_message_content(payload)
    if not content:
        raise HTTPException(status_code=400, detail="message is required")
    try:
        session, messages = await agent_planning_service.add_user_message(
            db,
            session=session,
            content=content,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="message is required") from exc
    return _agent_plan_intake_payload(session, messages)


@router.post("/sessions/{session_id}/generate")
async def generate_agent_plan_session_alias(
    session_id: str,
    db: DbSession,
    user: CurrentUser,
):
    session = await _load_owned_session(session_id, db, user)
    messages = await agent_planning_service.list_messages(db, session_id=session.id)
    return _agent_plan_generate_payload(session, messages)


@router.get("/sessions/{session_id}")
async def get_agent_plan_session_alias(session_id: str, db: DbSession, user: CurrentUser):
    session = await _load_owned_session(session_id, db, user)
    messages = await agent_planning_service.list_messages(db, session_id=session.id)
    return _agent_plan_session_alias_payload(session, messages)


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
    task = await _create_run_from_plan_session(session, db, user)
    messages = await agent_planning_service.list_messages(db, session_id=session.id)
    run_detail = parse_task_detail(task)
    run_detail.pop("execution_log", None)
    return {
        "session": redacted_plan_session_payload(session, messages),
        "run": json.loads(json.dumps(run_detail, ensure_ascii=False, default=str)),
    }


@router.post("/{session_id}/create-run", response_model=AgentPlanCreateRunResponse)
async def create_run_from_current_plan(
    session_id: str,
    db: DbSession,
    user: CurrentUser,
):
    session = await _load_owned_session(session_id, db, user)
    task = await _create_run_from_plan_session(session, db, user)
    return _agent_plan_run_link(task.id)

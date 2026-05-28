from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, AsyncIterator, Awaitable, Callable

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.dependencies import CurrentUser, DbSession
from app.core.redaction import redact_sensitive_data, redact_sensitive_text
from app.models.agent_planning import AgentPlan, AgentPlanningMessage, AgentPlanningSession
from app.models.task import Task
from app.schemas.task import parse_task_detail
from app.services.agent_planning import (
    PLAN_SESSION_COLLECTING,
    PLAN_SESSION_EXECUTED,
    PLAN_SESSION_READY,
    _build_basic_plan,
    _missing_questions,
    agent_planning_service,
    normalize_planner_run_payload,
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
    action: str | None = None


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
STRUCTURED_INTAKE_STEP_IDS = [
    "target_kind",
    "coverage_scope",
    "auth_boundary",
    "safety_boundary",
    "success_criteria",
]
STRUCTURED_TO_LEGACY_STEP = {
    "target_kind": "target",
    "coverage_scope": "scope",
    "auth_boundary": "auth",
    "safety_boundary": "safety",
    "success_criteria": "success",
}
STRUCTURED_FIELD_BY_STEP = {
    "target_kind": "target_json",
    "coverage_scope": "scope_json",
    "auth_boundary": "auth_json",
    "safety_boundary": "safety_json",
    "success_criteria": "success_json",
}
STRUCTURED_REQUIRED_STEPS = {"target_kind", "auth_boundary", "safety_boundary"}


async def _augment_session_payload_with_structured_intake(
    payload: dict[str, Any],
    session: AgentPlanningSession,
    db: DbSession,
) -> dict[str, Any]:
    """Surface `structured_intake` and a structured-aware `current_step`.

    The chat-only endpoints (`/messages`, edit, delete) historically returned
    `current_step = "target"` and no `structured_intake` even when the user
    already confirmed earlier steps via the structured intake endpoint. That
    desynced the frontend stepper from real progress (live audit bug 1.13).
    """
    plan = await _load_structured_plan(db, session)
    if plan is None:
        return payload
    structured = redact_sensitive_data(
        {step: _structured_plan_step_data(plan, step) for step in STRUCTURED_INTAKE_STEP_IDS}
    )
    payload["structured_intake"] = structured
    if session.status not in {PLAN_SESSION_READY, PLAN_SESSION_EXECUTED}:
        payload["current_step"] = _structured_plan_current_step(plan, session)
    return payload


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


def _canonical_intake_step(value: Any) -> str | None:
    normalized = str(value or "").strip().lower()
    aliases = {
        "target": "target_kind",
        "target_kind": "target_kind",
        "target_type": "target_kind",
        "source": "target_kind",
        "scope": "coverage_scope",
        "coverage": "coverage_scope",
        "coverage_scope": "coverage_scope",
        "auth": "auth_boundary",
        "login": "auth_boundary",
        "credentials": "auth_boundary",
        "auth_boundary": "auth_boundary",
        "safety": "safety_boundary",
        "policy": "safety_boundary",
        "safety_boundary": "safety_boundary",
        "success": "success_criteria",
        "criteria": "success_criteria",
        "success_criteria": "success_criteria",
    }
    return aliases.get(normalized)


def _structured_step_required(step: str) -> bool:
    return step in STRUCTURED_REQUIRED_STEPS


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


def _is_structured_intake(payload: AgentPlanIntakeRequest) -> bool:
    return bool(
        payload.current_step
        or payload.selected_option is not None
        or str(payload.action or "").strip().lower() in {"continue", "defer", "skip"}
    )


async def _load_or_create_structured_plan(
    db: DbSession,
    session: AgentPlanningSession,
) -> AgentPlan:
    result = await db.execute(select(AgentPlan).where(AgentPlan.session_id == session.id))
    plan = result.scalars().first()
    if plan is not None:
        return plan
    plan = AgentPlan(
        session_id=session.id,
        title=session.title or "新计划",
        objective="",
        status=PLAN_SESSION_COLLECTING,
    )
    db.add(plan)
    await db.flush()
    return plan


async def _load_structured_plan(
    db: DbSession,
    session: AgentPlanningSession,
) -> AgentPlan | None:
    result = await db.execute(select(AgentPlan).where(AgentPlan.session_id == session.id))
    return result.scalars().first()


def _structured_step_payload(payload: AgentPlanIntakeRequest) -> dict[str, Any]:
    action = str(payload.action or "continue").strip().lower() or "continue"
    step = _canonical_intake_step(payload.current_step) or "target_kind"
    selected = redact_sensitive_data(payload.selected_option)
    message = _intake_text(payload.message, limit=900) or ""
    option_summary = _selected_option_summary(selected, step)
    if action == "skip":
        status = "skipped"
        summary = f"{INTAKE_FIELD_LABELS[STRUCTURED_TO_LEGACY_STEP[step]]}：已跳过"
    elif action == "defer":
        status = "deferred"
        summary = f"{INTAKE_FIELD_LABELS[STRUCTURED_TO_LEGACY_STEP[step]]}：稍后补充"
    else:
        status = "confirmed"
        summary = option_summary or message
    if message and option_summary and message not in option_summary:
        summary = f"{option_summary}\n{message}"
    value = selected.get("value") if isinstance(selected, dict) else None
    label = selected.get("label") if isinstance(selected, dict) else None
    return redact_sensitive_data(
        {
            "step": step,
            "status": status,
            "action": action,
            "label": label,
            "value": value,
            "message": option_summary,
            "supplement": message,
            "summary": summary,
        }
    )


def _structured_plan_step_data(plan: AgentPlan, step: str) -> dict[str, Any]:
    field_name = STRUCTURED_FIELD_BY_STEP[step]
    value = getattr(plan, field_name, None)
    return value if isinstance(value, dict) else {}


def _structured_plan_content(plan: AgentPlan) -> str:
    lines: list[str] = []
    for step in STRUCTURED_INTAKE_STEP_IDS:
        data = _structured_plan_step_data(plan, step)
        if not data:
            continue
        label = INTAKE_FIELD_LABELS[STRUCTURED_TO_LEGACY_STEP[step]]
        summary = _intake_text(data.get("summary"), limit=900)
        if summary:
            lines.append(f"{label}：{summary}")
    return "\n".join(lines).strip()


def _structured_step_is_handled(plan: AgentPlan, step: str) -> bool:
    data = _structured_plan_step_data(plan, step)
    return bool(data and data.get("status") in {"confirmed", "deferred", "skipped"})


def _structured_plan_ready_for_generation(plan: AgentPlan) -> bool:
    for step in STRUCTURED_INTAKE_STEP_IDS:
        if not _structured_step_is_handled(plan, step):
            return False
    for step in STRUCTURED_REQUIRED_STEPS:
        if _structured_plan_step_data(plan, step).get("status") != "confirmed":
            return False
    return True


def _structured_plan_current_step(plan: AgentPlan, session: AgentPlanningSession) -> str:
    if session.status == PLAN_SESSION_EXECUTED:
        return "executed"
    if session.status == PLAN_SESSION_READY or session.current_run_payload:
        return "review"
    for step in STRUCTURED_INTAKE_STEP_IDS:
        if not _structured_step_is_handled(plan, step):
            return STRUCTURED_TO_LEGACY_STEP[step]

    content = _structured_plan_content(plan)
    if content:
        fake_messages = [
            AgentPlanningMessage(session_id=session.id, role="user", content=content)
        ]
        payload = normalize_planner_run_payload({}, fake_messages)
        if not payload.source:
            return "target"
        missing_text = "\n".join(_missing_questions(payload))
        if "登录" in missing_text or "鉴权" in missing_text or "Token" in missing_text:
            return "auth"
        if "安全" in missing_text:
            return "safety"
        if "成功" in missing_text or "断言" in missing_text:
            return "success"

    for step in STRUCTURED_INTAKE_STEP_IDS:
        data = _structured_plan_step_data(plan, step)
        if _structured_step_required(step) and data.get("status") != "confirmed":
            return STRUCTURED_TO_LEGACY_STEP[step]
    return "success"


def _structured_question_for_step(step: str) -> dict[str, Any] | None:
    option_groups = {
        "target_kind": {
            "question": "要先确定哪类测试目标？",
            "required": True,
            "options": [
                {
                    "label": "API / 接口",
                    "title": "API / OpenAPI",
                    "description": "用于接口文档、接口契约、只读接口覆盖或指定接口回归。",
                    "field": "target_kind",
                    "value": "api_openapi",
                    "step": "target_kind",
                    "message": "测试目标类型：API / OpenAPI/Swagger 接口来源。",
                },
                {
                    "label": "Web UI / 网页",
                    "title": "Web UI 页面",
                    "description": "用于浏览器页面、登录后业务流程、表单和页面可用性检查。",
                    "field": "target_kind",
                    "value": "web_page",
                    "step": "target_kind",
                    "message": "测试目标类型：浏览器 Web UI 页面。",
                },
                {
                    "label": "自定义",
                    "title": "自定义目标",
                    "description": "用补充说明描述具体目标，但仍限定在 API 或浏览器 Web UI 范围内。",
                    "field": "target_kind",
                    "value": "custom",
                    "step": "target_kind",
                    "message": "测试目标类型：自定义 API/Web UI 目标，由补充说明限定。",
                },
            ],
        },
        "coverage_scope": {
            "question": "先按哪个测试范围规划？",
            "required": False,
            "options": [
                {
                    "label": "冒烟范围",
                    "title": "冒烟检查",
                    "description": "优先覆盖关键入口、基础可用性和发布前阻断风险。",
                    "field": "coverage_scope",
                    "value": "smoke",
                    "step": "coverage_scope",
                    "allows_skip": True,
                    "optional": True,
                    "message": "覆盖范围：关键路径和基础可用性冒烟检查。",
                },
                {
                    "label": "回归范围",
                    "title": "回归范围",
                    "description": "覆盖核心流程、主要回归风险和历史问题区域。",
                    "field": "coverage_scope",
                    "value": "regression",
                    "step": "coverage_scope",
                    "allows_skip": True,
                    "optional": True,
                    "message": "覆盖范围：核心流程、主要回归风险和历史问题。",
                },
                {
                    "label": "接口契约",
                    "title": "接口契约",
                    "description": "适合 OpenAPI/Swagger 输入，关注文档契约、状态码和响应结构。",
                    "field": "coverage_scope",
                    "value": "api_contract",
                    "step": "coverage_scope",
                    "allows_skip": True,
                    "optional": True,
                    "message": "覆盖范围：接口契约、状态码和响应结构检查。",
                },
            ],
        },
        "auth_boundary": {
            "question": "目标的登录或鉴权边界是什么？",
            "required": True,
            "options": [
                {
                    "label": "无需登录",
                    "title": "公开访问",
                    "description": "目标可匿名访问，计划按无需登录或鉴权处理。",
                    "field": "auth_boundary",
                    "value": "no_auth",
                    "step": "auth_boundary",
                    "message": "登录方式/凭证：目标公开访问，无需登录或鉴权。",
                },
                {
                    "label": "提供账号",
                    "title": "登录流程",
                    "description": "使用测试账号、密码、验证码说明或登录步骤完成登录。",
                    "field": "auth_boundary",
                    "value": "login_flow",
                    "step": "auth_boundary",
                    "message": "登录方式/凭证：目标需要登录流程和测试账号。",
                },
                {
                    "label": "手动鉴权",
                    "title": "Token / Header",
                    "description": "使用 Token、Cookie 或 Header 作为 API/UI 访问凭证。",
                    "field": "auth_boundary",
                    "value": "manual_auth",
                    "step": "auth_boundary",
                    "message": "登录方式/凭证：使用手动提供的 Token、Cookie 或 Header。",
                },
            ],
        },
        "safety_boundary": {
            "question": "安全边界是什么？",
            "required": True,
            "options": [
                {
                    "label": "只读边界",
                    "title": "只读检查",
                    "description": "不创建、修改或删除数据；API 默认限制为安全只读方法。",
                    "field": "safety_boundary",
                    "value": "safe_read_only",
                    "step": "safety_boundary",
                    "message": "安全边界：只做只读检查，不创建、修改或删除数据。",
                },
                {
                    "label": "鉴权只读",
                    "title": "带鉴权只读",
                    "description": "允许携带凭证访问受保护资源，但仍不执行写入动作。",
                    "field": "safety_boundary",
                    "value": "safe_with_auth",
                    "step": "safety_boundary",
                    "message": "安全边界：允许带鉴权只读访问，不执行写入动作。",
                },
                {
                    "label": "测试环境写入",
                    "title": "允许测试写入",
                    "description": "仅限测试环境，并在约定范围内创建、修改或删除测试数据。",
                    "field": "safety_boundary",
                    "value": "write_allowed",
                    "step": "safety_boundary",
                    "message": "安全边界：测试环境允许在约定范围内写入测试数据。",
                },
            ],
        },
        "success_criteria": {
            "question": "结果怎样才算成功？",
            "required": False,
            "options": [
                {
                    "label": "证据充分",
                    "title": "证据充分",
                    "description": "每个覆盖点都需要结果、证据、失败原因或明确跳过原因。",
                    "field": "success_criteria",
                    "value": "evidence_complete",
                    "step": "success_criteria",
                    "allows_skip": True,
                    "optional": True,
                    "message": "成功标准：每个覆盖点都有结果、证据、失败原因或明确跳过原因。",
                },
                {
                    "label": "阻断优先",
                    "title": "阻断问题优先",
                    "description": "优先发现发布阻断问题，并给出可复现步骤和证据。",
                    "field": "success_criteria",
                    "value": "blocking_findings",
                    "step": "success_criteria",
                    "allows_skip": True,
                    "optional": True,
                    "message": "成功标准：优先发现发布阻断问题，并提供可复现证据。",
                },
            ],
        },
    }
    group = option_groups.get(step)
    if group is None:
        return None
    return redact_sensitive_data({**group, "step": step})


def _structured_missing_info(plan: AgentPlan, session: AgentPlanningSession) -> list[dict[str, Any]]:
    if session.status == PLAN_SESSION_READY:
        return []
    current = _structured_plan_current_step(plan, session)
    if current in {"review", "executed"}:
        return []
    canonical = _canonical_intake_step(current)
    if not canonical:
        return []
    group = _structured_question_for_step(canonical)
    return [
        {
            "key": current,
            "label": INTAKE_FIELD_LABELS.get(current, current),
            "required": bool(group.get("required", True)) if group else True,
        }
    ]


def _structured_draft(plan: AgentPlan) -> dict[str, dict[str, Any]]:
    draft: dict[str, dict[str, Any]] = {}
    for step in STRUCTURED_INTAKE_STEP_IDS:
        legacy = STRUCTURED_TO_LEGACY_STEP[step]
        data = _structured_plan_step_data(plan, step)
        status = str(data.get("status") or "")
        if status == "confirmed":
            item_status = "confirmed"
        elif status == "deferred":
            item_status = "pending"
        elif status == "skipped":
            item_status = "skipped"
        else:
            item_status = "pending"
        draft[legacy] = {
            "value": data.get("summary") if data else None,
            "status": item_status,
        }
    return redact_sensitive_data(draft)


def _update_session_from_structured_plan(
    session: AgentPlanningSession,
    plan: AgentPlan,
) -> None:
    content = _structured_plan_content(plan)
    if not content:
        session.status = PLAN_SESSION_COLLECTING
        session.current_plan = None
        session.current_run_payload = None
        return
    if not _structured_plan_ready_for_generation(plan):
        session.status = PLAN_SESSION_COLLECTING
        session.current_plan = None
        session.current_run_payload = None
        plan.status = PLAN_SESSION_COLLECTING
        plan.objective = content
        plan.recommended_run_payload_json = None
        return
    fake_messages = [
        AgentPlanningMessage(session_id=session.id, role="user", content=content)
    ]
    payload = normalize_planner_run_payload({}, fake_messages)
    questions = _missing_questions(payload)
    if questions or not payload.source:
        session.status = PLAN_SESSION_COLLECTING
        session.current_plan = None
        session.current_run_payload = None
        plan.status = PLAN_SESSION_COLLECTING
        plan.objective = payload.objective
        plan.recommended_run_payload_json = None
        return
    current_plan = _build_basic_plan(payload)
    run_payload = payload.model_dump(mode="json", exclude_none=True)
    session.status = PLAN_SESSION_READY
    session.current_plan = json.dumps(current_plan, ensure_ascii=False, default=str)
    session.current_run_payload = json.dumps(run_payload, ensure_ascii=False, default=str)
    session.rejection_reason = None
    plan.status = PLAN_SESSION_READY
    plan.title = _intake_text(current_plan.get("target"), limit=160) or session.title
    plan.objective = payload.objective
    plan.api_plan_json = current_plan if payload.test_type == "api" else None
    plan.ui_plan_json = current_plan if payload.test_type == "ui" else None
    plan.recommended_run_payload_json = redact_sensitive_data(run_payload)


def _structured_intake_payload(
    session: AgentPlanningSession,
    messages: list[AgentPlanningMessage],
    plan: AgentPlan,
) -> dict[str, Any]:
    session_payload = _agent_plan_session_alias_payload(session, messages)
    current = _structured_plan_current_step(plan, session)
    canonical = _canonical_intake_step(current)
    group = None if session.status == PLAN_SESSION_READY else (
        _structured_question_for_step(canonical) if canonical else None
    )
    session_payload["current_step"] = current
    session_payload["structured_intake"] = redact_sensitive_data(
        {
            step: _structured_plan_step_data(plan, step)
            for step in STRUCTURED_INTAKE_STEP_IDS
        }
    )
    session_payload["question_options"] = [group] if group else []
    return {
        "extracted": _agent_plan_extracted(session_payload),
        "draft": _structured_draft(plan),
        "next_question": _agent_plan_next_question([group] if group else []),
        "missing_info": _structured_missing_info(plan, session),
        "session": session_payload,
    }


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
    db: DbSession | None = None,
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
        session_payload = redacted_plan_session_payload(session, messages)
        if db is not None:
            session_payload = await _augment_session_payload_with_structured_intake(
                session_payload, session, db
            )
        yield _sse_event(
            "final",
            {
                "session": session_payload,
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
    if _is_structured_intake(payload):
        step = _canonical_intake_step(payload.current_step)
        if step is None:
            raise HTTPException(status_code=400, detail="current_step is required")
        action = str(payload.action or "continue").strip().lower() or "continue"
        if action not in {"continue", "defer", "skip"}:
            raise HTTPException(status_code=400, detail="unsupported intake action")
        if action == "skip" and _structured_step_required(step):
            raise HTTPException(status_code=400, detail="current_step cannot be skipped")
        if action == "continue" and payload.selected_option is None and not str(payload.message or "").strip():
            raise HTTPException(status_code=400, detail="message is required")
        plan = await _load_structured_plan(db, session)
        messages = await agent_planning_service.list_messages(db, session_id=session.id)
        if plan is None and messages and action == "continue":
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

        if plan is None:
            plan = await _load_or_create_structured_plan(db, session)
        setattr(plan, STRUCTURED_FIELD_BY_STEP[step], _structured_step_payload(payload))
        plan.updated_at = datetime.utcnow()
        session.updated_at = datetime.utcnow()
        _update_session_from_structured_plan(session, plan)
        await db.commit()
        await db.refresh(session)
        await db.refresh(plan)
        messages = await agent_planning_service.list_messages(db, session_id=session.id)
        return _structured_intake_payload(session, messages, plan)

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
    response_payload = redacted_plan_session_payload(session, messages)
    return await _augment_session_payload_with_structured_intake(response_payload, session, db)


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
    response_payload = redacted_plan_session_payload(session, messages)
    return await _augment_session_payload_with_structured_intake(response_payload, session, db)


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

    return StreamingResponse(_stream_turn_events(action, db), media_type="text/event-stream")


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
    response_payload = redacted_plan_session_payload(session, messages)
    return await _augment_session_payload_with_structured_intake(response_payload, session, db)


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

    return StreamingResponse(_stream_turn_events(action, db), media_type="text/event-stream")


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
    response_payload = redacted_plan_session_payload(session, messages)
    return await _augment_session_payload_with_structured_intake(response_payload, session, db)


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
    response_payload = redacted_plan_session_payload(session, messages)
    return await _augment_session_payload_with_structured_intake(response_payload, session, db)


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

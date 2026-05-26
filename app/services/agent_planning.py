from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

import yaml
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.json_utils import parse_llm_json_object
from app.agent.nodes.source_loader import classify_input
from app.core.llm_gateway import llm_gateway
from app.core.redaction import redact_sensitive_data, redact_sensitive_text
from app.models.agent_planning import AgentPlanningMessage, AgentPlanningSession

logger = logging.getLogger(__name__)

PLAN_SESSION_COLLECTING = "collecting"
PLAN_SESSION_READY = "ready"
PLAN_SESSION_EXECUTED = "executed"

ALLOWED_TEST_TYPES = {"api", "ui"}
ALLOWED_AUTH_MODES = {"auto", "manual", "none_confirmed"}
ALLOWED_CAPTCHA_MODES = {"none", "static", "dynamic"}
ALLOWED_API_POLICIES = {"safe_read_only", "safe_with_auth", "write_allowed"}

_URL_RE = re.compile(r"https?://[^\s<>'\"`)\]},，;；。]+", re.I)
_LABEL_SEPARATOR_RE = r"(?:\s*(?:[:=：]|是|为)\s*|\s+)"
_TEXT_VALUE_RE = r"([^\s,，;；。]+)"
_USERNAME_RE = re.compile(
    rf"(?i)(?:\b(?:username|user|account|login(?:_?name)?|email)\b|用户名|账号|账户|登录名)"
    rf"{_LABEL_SEPARATOR_RE}{_TEXT_VALUE_RE}"
)
_PASSWORD_RE = re.compile(
    rf"(?i)(?:\b(?:password|passwd|pwd)\b|密码){_LABEL_SEPARATOR_RE}{_TEXT_VALUE_RE}"
)
_CAPTCHA_RE = re.compile(
    rf"(?i)(?:\b(?:captcha|otp|mfa|code)\b|验证码|动态码|一次性码)"
    rf"{_LABEL_SEPARATOR_RE}{_TEXT_VALUE_RE}"
)
_TOKEN_RE = re.compile(r"(?i)\bBearer\s+([A-Za-z0-9._~+/=-]+)")
_KEY_VALUE_TOKEN_RE = re.compile(
    r"(?i)\b(?:token|access_token|api_key|apikey)\b\s*(?:[:=：]|是|为)?\s*([^\s,，;；]+)"
)
_API_INTENT_RE = re.compile(r"(?i)\b(api|openapi|swagger|endpoint)\b|接口")
_UI_INTENT_RE = re.compile(r"(?i)\b(ui|browser|page|web|login page|screen)\b|页面|浏览器|管理后台|后台")
_NO_AUTH_RE = re.compile(r"(?i)\b(no auth|public)\b|无需鉴权|公开|不需要登录")
_DYNAMIC_CAPTCHA_RE = re.compile(r"(?i)dynamic captcha|动态验证码|图片验证码")
_STATIC_CAPTCHA_RE = re.compile(r"(?i)static captcha|fixed captcha|固定验证码|验证码")
_SAFE_WITH_AUTH_RE = re.compile(r"(?i)authenticated read|read-only with auth|带鉴权只读|鉴权只读")
_SAFE_READ_ONLY_RE = re.compile(
    r"(?i)\bread[- ]?only\b|\bsafe read\b|只读|不要修改|不要删除|不能保存|不要保存|不可保存|不保存"
)
_WRITE_ALLOWED_RE = re.compile(
    r"(?i)\ballow(?:ed)? write\b|\bwrite_allowed\b|"
    r"\btest environment\b.*\b(create|modify|delete|write)\b|"
    r"允许写入|允许.*(创建|修改|删除)|测试环境.*可以.*(创建|修改|删除)|可以.*(创建|修改|删除)"
)


class PlannerAuthCredentials(BaseModel):
    model_config = ConfigDict(extra="ignore")

    username: str | None = None
    password: str | None = None
    captcha: str | None = None


class PlannerAuthAcquireConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    username: str | None = None
    password: str | None = None
    captcha: str | None = None
    tenant: str | None = None
    login_url: str | None = None
    captcha_url: str | None = None
    method: str = "POST"
    content_type: str = "json"
    headers: dict[str, Any] | None = None
    body: dict[str, Any] | None = None
    token_path: str | None = None
    header_name: str = "Authorization"
    token_prefix: str = "Bearer"


class PlannerRunPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source: str | None = None
    test_type: str = "api"
    objective: str = ""
    base_url: str | None = None
    headers: dict[str, Any] | None = None
    token: str | None = None
    auth_mode: str = "auto"
    captcha_mode: str = "none"
    auth_credentials: PlannerAuthCredentials | None = None
    auth_config: PlannerAuthAcquireConfig | None = None
    api_execution_policy: str = "safe_read_only"
    allow_out_of_schema_api_cases: bool = False
    setup_instructions: str = ""


class PlannerLLMOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    response: str | None = None
    message: str | None = None
    status: str = PLAN_SESSION_COLLECTING
    questions: list[str] = Field(default_factory=list)
    ready_to_execute: bool = False
    plan: dict[str, Any] | None = None
    run_payload: dict[str, Any] | None = None


class PlannerTurnResult(BaseModel):
    message: str
    status: str
    questions: list[str] = Field(default_factory=list)
    ready_to_execute: bool = False
    plan: dict[str, Any] | None = None
    run_payload: dict[str, Any] | None = None


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def parse_json_object_text(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _clean_text(value: Any, *, limit: int = 800) -> str:
    return " ".join(str(value or "").split())[:limit]


def _clean_url(value: Any) -> str | None:
    text = str(value or "").strip().strip(".,;，；。")
    if text.startswith(("http://", "https://")):
        return text[:1000]
    return None


def _latest_url(text: str) -> str | None:
    matches = list(_URL_RE.finditer(text))
    return _clean_url(matches[-1].group(0)) if matches else None


def _source_from_text(text: str) -> str | None:
    openapi_source = _extract_openapi_source(text)
    if openapi_source:
        return openapi_source[:20000]
    return _latest_url(text)


def _extract_openapi_source(text: str) -> str | None:
    stripped = text.strip()
    if not stripped:
        return None
    if stripped.startswith("{") and any(marker in stripped[:1000] for marker in ('"openapi"', '"swagger"')):
        parsed = parse_llm_json_object(stripped)
        if parsed.get("paths") and (parsed.get("openapi") or parsed.get("swagger")):
            return _json_dumps(parsed)
    if stripped.startswith(("openapi:", "swagger:")) and "paths:" in stripped:
        return stripped
    parsed = parse_llm_json_object(stripped)
    if parsed.get("paths") and (parsed.get("openapi") or parsed.get("swagger")):
        return _json_dumps(parsed)
    return None


def _extract_base_url_from_openapi_source(source: str | None) -> str | None:
    if not source:
        return None
    try:
        document = yaml.safe_load(source)
    except Exception:
        return None
    if not isinstance(document, dict):
        return None
    servers = document.get("servers")
    if isinstance(servers, list):
        for server in servers:
            if isinstance(server, dict):
                url = _clean_url(server.get("url"))
                if url:
                    return url
    host = str(document.get("host") or "").strip()
    if host:
        scheme = "https"
        schemes = document.get("schemes")
        if isinstance(schemes, list) and schemes:
            scheme = str(schemes[0] or "https").strip() or "https"
        base_path = str(document.get("basePath") or "").strip()
        return f"{scheme}://{host}{base_path}"
    return None


def _latest_user_source(messages: list[AgentPlanningMessage]) -> str | None:
    for message in reversed(messages):
        if message.role != "user":
            continue
        source = _source_from_text(message.content)
        if source:
            return source
    return None


def _latest_user_text(messages: list[AgentPlanningMessage]) -> str:
    for message in reversed(messages):
        if message.role == "user" and message.content.strip():
            return message.content
    return ""


def _candidate_source(
    raw_payload: dict[str, Any],
    messages: list[AgentPlanningMessage],
    conversation_text: str,
) -> str | None:
    latest_user_source = _latest_user_source(messages)
    if latest_user_source:
        return latest_user_source
    source = str(raw_payload.get("source") or "").strip()
    if source:
        return source[:20000]
    return _source_from_text(conversation_text)


def _infer_test_type(raw_payload: dict[str, Any], source: str | None, text: str) -> str:
    requested = str(raw_payload.get("test_type") or "").strip().lower()
    if requested in ALLOWED_TEST_TYPES:
        return requested
    lowered = text.lower()
    if source:
        input_type = classify_input(source)
        if input_type in {"swagger_url", "swagger_json", "swagger_yaml"}:
            return "api"
        if _API_INTENT_RE.search(lowered):
            return "api"
    if _UI_INTENT_RE.search(lowered):
        return "ui"
    return "api"


def _last_regex_value(pattern: re.Pattern[str], text: str) -> str | None:
    last_match = None
    for match in pattern.finditer(text):
        last_match = match
    if last_match is None:
        return None
    return last_match.group(1).strip().strip("'\"")


def _credentials_from_text(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    username = _last_regex_value(_USERNAME_RE, text)
    password = _last_regex_value(_PASSWORD_RE, text)
    captcha = _last_regex_value(_CAPTCHA_RE, text)
    if username:
        values["username"] = username
    if password:
        values["password"] = password
    if captcha:
        values["captcha"] = captcha
    return values


def _credentials_from_payload(raw: Any) -> dict[str, str]:
    values: dict[str, str] = {}
    if isinstance(raw, dict):
        for key in ("username", "password", "captcha"):
            value = str(raw.get(key) or "").strip()
            if value:
                values[key] = value
    return values


def _merge_credentials(raw_payload: dict[str, Any], text: str) -> PlannerAuthCredentials | None:
    values = _credentials_from_payload(raw_payload.get("auth_credentials"))
    values.update({key: value for key, value in _credentials_from_text(text).items() if value})
    return PlannerAuthCredentials(**values) if values else None


def _token_from_payload_or_text(raw_payload: dict[str, Any], text: str) -> str | None:
    token = str(raw_payload.get("token") or "").strip()
    if token:
        return token
    bearer = _last_regex_value(_TOKEN_RE, text)
    if bearer:
        return bearer
    return _last_regex_value(_KEY_VALUE_TOKEN_RE, text)


def _headers_from_payload(raw: Any) -> dict[str, Any] | None:
    return raw if isinstance(raw, dict) and raw else None


def _auth_config_from_payload(raw: Any, credentials: PlannerAuthCredentials | None) -> PlannerAuthAcquireConfig | None:
    config = raw if isinstance(raw, dict) else {}
    if not config and credentials is None:
        return None
    data = dict(config)
    if credentials is not None:
        credential_data = credentials.model_dump(exclude_none=True)
        for key, value in credential_data.items():
            data.setdefault(key, value)
        if credential_data.get("username") and credential_data.get("password"):
            data.setdefault("enabled", True)
    return PlannerAuthAcquireConfig(**data)


def _normalize_choice(value: Any, allowed: set[str], default: str) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in allowed else default


def _infer_auth_mode(
    raw_payload: dict[str, Any],
    *,
    token: str | None,
    headers: dict[str, Any] | None,
    credentials: PlannerAuthCredentials | None,
    text: str,
) -> str:
    requested = str(raw_payload.get("auth_mode") or "").strip().lower()
    if requested in ALLOWED_AUTH_MODES:
        return requested
    lowered = text.lower()
    if _NO_AUTH_RE.search(lowered):
        return "none_confirmed"
    if token or headers:
        return "manual"
    if credentials and credentials.username and credentials.password:
        return "auto"
    return "auto"


def _infer_captcha_mode(raw_payload: dict[str, Any], credentials: PlannerAuthCredentials | None, text: str) -> str:
    requested = _normalize_choice(raw_payload.get("captcha_mode"), ALLOWED_CAPTCHA_MODES, "")
    if requested:
        return requested
    lowered = text.lower()
    if _DYNAMIC_CAPTCHA_RE.search(lowered):
        return "dynamic"
    if _STATIC_CAPTCHA_RE.search(lowered) or (credentials and credentials.captcha):
        return "static"
    return "none"


def _infer_api_policy(raw_payload: dict[str, Any], text: str) -> str:
    requested = _normalize_choice(
        raw_payload.get("api_execution_policy"), ALLOWED_API_POLICIES, ""
    )
    if requested:
        return requested
    lowered = text.lower()
    if _SAFE_WITH_AUTH_RE.search(lowered):
        return "safe_with_auth"
    if _SAFE_READ_ONLY_RE.search(lowered):
        return "safe_read_only"
    if _WRITE_ALLOWED_RE.search(lowered):
        return "write_allowed"
    return "safe_read_only"


def normalize_planner_run_payload(
    raw_payload: dict[str, Any] | None,
    messages: list[AgentPlanningMessage],
) -> PlannerRunPayload:
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    conversation_text = "\n".join(
        message.content for message in messages if message.role in {"user", "system"}
    )
    latest_user_text = _latest_user_text(messages)
    source = _candidate_source(payload, messages, conversation_text)
    source_input_type = classify_input(source) if source else "unknown"
    base_url = _clean_url(payload.get("base_url")) or _extract_base_url_from_openapi_source(source)
    intent_text = latest_user_text or conversation_text
    test_type = _infer_test_type(payload, source, intent_text)
    credentials = _merge_credentials(payload, conversation_text)
    token = _token_from_payload_or_text(payload, conversation_text)
    headers = _headers_from_payload(payload.get("headers"))
    auth_config = _auth_config_from_payload(payload.get("auth_config"), credentials)
    auth_mode = _infer_auth_mode(
        payload,
        token=token,
        headers=headers,
        credentials=credentials,
        text=intent_text,
    )
    objective = _clean_text(payload.get("objective") or redact_sensitive_text(conversation_text), limit=500)
    if not objective:
        objective = "对目标执行安全的 TestClaw 智能体检查。"
    setup_instructions = _clean_text(
        payload.get("setup_instructions") or redact_sensitive_text(conversation_text),
        limit=2000,
    )

    if source_input_type in {"swagger_json", "swagger_yaml"} and not base_url:
        base_url = _clean_url(payload.get("target_url"))

    return PlannerRunPayload(
        source=source,
        test_type=test_type,
        objective=objective,
        base_url=base_url,
        headers=headers,
        token=token,
        auth_mode=auth_mode,
        captcha_mode=_infer_captcha_mode(payload, credentials, intent_text),
        auth_credentials=credentials,
        auth_config=auth_config,
        api_execution_policy=_infer_api_policy(payload, intent_text),
        allow_out_of_schema_api_cases=bool(payload.get("allow_out_of_schema_api_cases", False)),
        setup_instructions=setup_instructions,
    )


def _missing_questions(payload: PlannerRunPayload) -> list[str]:
    if not payload.source:
        return ["TestClaw 应测试哪个目标？请粘贴 URL 或 OpenAPI/Swagger 来源。"]
    input_type = classify_input(payload.source)
    if input_type in {"swagger_json", "swagger_yaml"} and not payload.base_url:
        return ["执行这份 API 文档时应使用哪个基础 URL？"]
    return []


def _plan_auth_summary(payload: PlannerRunPayload) -> str:
    if payload.auth_mode == "manual":
        return "手动 Token/Header 会在启动前通过预检验证。"
    if payload.auth_mode == "none_confirmed":
        return "无需鉴权访问会在启动前通过只读预检确认。"
    if payload.auth_credentials:
        return "自动鉴权会使用已提供的登录凭据，并执行服务端预检。"
    return "已选择自动鉴权；如果目标受保护，预检会提示补充凭据。"


def _build_basic_plan(payload: PlannerRunPayload, raw_plan: dict[str, Any] | None = None) -> dict[str, Any]:
    source_label = payload.base_url or payload.source or "未指定目标"
    raw_plan = raw_plan if isinstance(raw_plan, dict) else {}
    steps = raw_plan.get("steps") if isinstance(raw_plan.get("steps"), list) else []
    scope = raw_plan.get("scope") if isinstance(raw_plan.get("scope"), list) else []
    if not scope:
        scope = [
            "识别目标并加载测试输入。",
            "复用现有 TestClaw 流程执行鉴权和运行准备预检。",
            "执行安全的智能体检查，并产出带证据的结果。",
        ]
    if not steps:
        steps = [
            "验证输入源、目标、模型、Worker、执行器和鉴权准备状态。",
            "基于已确认的目标和安全策略生成 API 或 UI 用例。",
            "启动当前 TestClaw 智能体任务，并在运行详情页查看进度。",
        ]
    return {
        "title": _clean_text(raw_plan.get("title") or "测试智能体任务计划", limit=120),
        "summary": _clean_text(
            raw_plan.get("summary")
            or f"{payload.test_type.upper()} 测试：{redact_sensitive_text(source_label)}",
            limit=500,
        ),
        "target": redact_sensitive_text(source_label),
        "test_type": payload.test_type,
        "objective": redact_sensitive_text(payload.objective),
        "scope": [redact_sensitive_text(_clean_text(item, limit=240)) for item in scope[:8]],
        "steps": [redact_sensitive_text(_clean_text(item, limit=240)) for item in steps[:8]],
        "safety": [
            "除非已明确允许写入，否则 API 执行默认采用安全只读策略。",
            "运行创建和鉴权验证会复用现有 TestClaw 预检流程。",
        ],
        "auth": _plan_auth_summary(payload),
        "blockers": [],
    }


def _sanitize_llm_output(raw: dict[str, Any]) -> PlannerLLMOutput | None:
    if not raw:
        return None
    try:
        return PlannerLLMOutput(**raw)
    except Exception as exc:
        logger.debug("Planner LLM JSON did not match contract: %s", exc)
        return None


class AgentPlanningService:
    async def create_session(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        title: str | None = None,
    ) -> AgentPlanningSession:
        session = AgentPlanningSession(
            user_id=user_id,
            title=_clean_text(title or "新计划", limit=160) or "新计划",
            status=PLAN_SESSION_COLLECTING,
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
        return session

    async def list_sessions(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        limit: int = 30,
    ) -> list[AgentPlanningSession]:
        stmt = (
            select(AgentPlanningSession)
            .where(AgentPlanningSession.user_id == user_id)
            .order_by(AgentPlanningSession.updated_at.desc())
            .limit(limit)
        )
        return list((await db.execute(stmt)).scalars())

    async def get_session(
        self,
        db: AsyncSession,
        *,
        session_id: str,
        user_id: str,
    ) -> AgentPlanningSession | None:
        session = await db.get(AgentPlanningSession, session_id)
        if session is None or session.user_id != user_id:
            return None
        return session

    async def list_messages(
        self,
        db: AsyncSession,
        *,
        session_id: str,
    ) -> list[AgentPlanningMessage]:
        stmt = (
            select(AgentPlanningMessage)
            .where(AgentPlanningMessage.session_id == session_id)
            .order_by(AgentPlanningMessage.created_at.asc())
        )
        return list((await db.execute(stmt)).scalars())

    async def add_user_message(
        self,
        db: AsyncSession,
        *,
        session: AgentPlanningSession,
        content: str,
    ) -> tuple[AgentPlanningSession, list[AgentPlanningMessage]]:
        content = content.strip()
        if not content:
            raise ValueError("content is required")
        user_message = AgentPlanningMessage(
            session_id=session.id,
            role="user",
            content=content,
        )
        db.add(user_message)
        await db.flush()

        messages = await self.list_messages(db, session_id=session.id)
        result = await self._generate_turn(db, session=session, messages=messages)
        assistant_message = AgentPlanningMessage(
            session_id=session.id,
            role="assistant",
            content=result.message,
            plan_json=_json_dumps(
                {
                    "status": result.status,
                    "questions": result.questions,
                    "ready_to_execute": result.ready_to_execute,
                    "plan": result.plan,
                    "run_payload": result.run_payload,
                }
            ),
        )
        db.add(assistant_message)
        session.status = result.status
        session.current_plan = _json_dumps(result.plan) if result.plan else None
        session.current_run_payload = (
            _json_dumps(result.run_payload) if result.ready_to_execute and result.run_payload else None
        )
        if result.ready_to_execute and result.plan:
            session.rejection_reason = None
        if session.title in {"New plan", "新计划"}:
            session.title = self._title_from_messages(messages, result)
        session.updated_at = datetime.utcnow()
        await db.commit()
        await db.refresh(session)
        return session, await self.list_messages(db, session_id=session.id)

    async def reject_plan(
        self,
        db: AsyncSession,
        *,
        session: AgentPlanningSession,
        reason: str | None = None,
    ) -> tuple[AgentPlanningSession, list[AgentPlanningMessage]]:
        clean_reason = reason.strip() if isinstance(reason, str) else ""
        session.current_plan = None
        session.current_run_payload = None
        session.status = PLAN_SESSION_COLLECTING
        session.rejection_reason = clean_reason or "计划已拒绝"
        session.updated_at = datetime.utcnow()
        message_text = (
            f"计划已拒绝。修订原因：{clean_reason}"
            if clean_reason
            else "计划已拒绝。等待新的修订说明。"
        )
        db.add(
            AgentPlanningMessage(
                session_id=session.id,
                role="system",
                content=message_text,
            )
        )
        await db.commit()
        await db.refresh(session)
        return session, await self.list_messages(db, session_id=session.id)

    async def _generate_turn(
        self,
        db: AsyncSession,
        *,
        session: AgentPlanningSession,
        messages: list[AgentPlanningMessage],
    ) -> PlannerTurnResult:
        raw_llm_output: PlannerLLMOutput | None = None
        try:
            raw_llm_output = await self._call_planner_llm(db, session=session, messages=messages)
        except Exception as exc:
            logger.info("Planner LLM unavailable, using local fallback: %s", exc)

        llm_output = raw_llm_output or PlannerLLMOutput(
            response="请先提供测试目标，我才能准备可执行计划。",
            status=PLAN_SESSION_COLLECTING,
            questions=[],
            ready_to_execute=False,
            run_payload={},
        )
        payload = normalize_planner_run_payload(llm_output.run_payload, messages)
        questions = _missing_questions(payload)
        ready = not questions and bool(payload.source)
        if ready and llm_output.ready_to_execute is False and llm_output.run_payload:
            ready = str(llm_output.status).lower() in {"ready", "ready_to_execute", "plan_ready"}
        if ready:
            plan = _build_basic_plan(payload, llm_output.plan)
            run_payload = payload.model_dump(mode="json", exclude_none=True)
            response = llm_output.response or llm_output.message
            if not response:
                response = "信息已足够，我已准备好这次运行计划。"
            return PlannerTurnResult(
                message=redact_sensitive_text(response),
                status=PLAN_SESSION_READY,
                questions=[],
                ready_to_execute=True,
                plan=plan,
                run_payload=run_payload,
            )

        if not questions:
            questions = llm_output.questions or [
                "TestClaw 应测试哪个目标？请粘贴 URL 或 OpenAPI/Swagger 来源。"
            ]
        message = llm_output.response or llm_output.message or "还需要补充一点信息。"
        if not llm_output.questions and questions:
            message = questions[0]
        return PlannerTurnResult(
            message=redact_sensitive_text(message),
            status=PLAN_SESSION_COLLECTING,
            questions=[redact_sensitive_text(question) for question in questions[:5]],
            ready_to_execute=False,
        )

    async def _call_planner_llm(
        self,
        db: AsyncSession,
        *,
        session: AgentPlanningSession,
        messages: list[AgentPlanningMessage],
    ) -> PlannerLLMOutput | None:
        llm = await llm_gateway.get_planner(db)
        history = [
            {
                "role": message.role,
                "content": redact_sensitive_text(message.content),
            }
            for message in messages[-16:]
        ]
        prompt = (
            "You are the TestClaw Plan Mode planner. Return strict JSON only. "
            "Do not include Markdown or hidden reasoning. Ask concise clarifying questions until "
            "source/target and execution intent are sufficient. When ready, emit a plan card and a "
            "run_payload for the existing TestClaw run API.\n\n"
            "Required JSON fields: response, status, questions, ready_to_execute, plan, run_payload.\n"
            "Allowed run_payload fields: source, test_type, objective, base_url, auth_mode, "
            "captcha_mode, auth_credentials, auth_config, token, headers, api_execution_policy, "
            "allow_out_of_schema_api_cases, setup_instructions.\n"
            "Allowed test_type: api or ui. Allowed auth_mode: auto, manual, none_confirmed. "
            "Allowed captcha_mode: none, static, dynamic. Allowed api_execution_policy: "
            "safe_read_only, safe_with_auth, write_allowed. Prefer safe_read_only unless the user "
            "explicitly approves writes in a test environment. Do not hardcode product names or "
            "vendor-specific assumptions. Never put raw secrets in response or plan text. Match the "
            "user's language for visible response, questions, and plan text; use concise Chinese "
            "when the user writes in Chinese.\n\n"
            f"Current rejection reason: {redact_sensitive_text(session.rejection_reason or '')}\n"
            f"Conversation JSON: {json.dumps(history, ensure_ascii=False, default=str)}"
        )
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        content = response.content if hasattr(response, "content") else str(response)
        return _sanitize_llm_output(parse_llm_json_object(str(content)))

    def _title_from_messages(
        self,
        messages: list[AgentPlanningMessage],
        result: PlannerTurnResult,
    ) -> str:
        if result.plan and result.plan.get("target"):
            return _clean_text(str(result.plan["target"]), limit=80) or "新计划"
        for message in messages:
            if message.role == "user" and message.content.strip():
                return _clean_text(redact_sensitive_text(message.content), limit=80) or "新计划"
        return "新计划"


agent_planning_service = AgentPlanningService()


def redacted_plan_session_payload(
    session: AgentPlanningSession,
    messages: list[AgentPlanningMessage] | None = None,
) -> dict[str, Any]:
    current_plan = parse_json_object_text(session.current_plan)
    current_run_payload = parse_json_object_text(session.current_run_payload)
    title = redact_sensitive_text(session.title)
    if title == "New plan":
        title = "新计划"
    payload: dict[str, Any] = {
        "id": session.id,
        "title": title,
        "status": session.status,
        "ready_to_execute": session.status == PLAN_SESSION_READY and bool(current_run_payload),
        "current_plan": redact_sensitive_data(current_plan) if current_plan else None,
        "current_run_payload": redact_sensitive_data(current_run_payload)
        if current_run_payload
        else None,
        "rejection_reason": redact_sensitive_text(session.rejection_reason or "")
        if session.rejection_reason
        else None,
        "executed_run_id": session.executed_run_id,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "updated_at": session.updated_at.isoformat() if session.updated_at else None,
    }
    if messages is not None:
        payload["messages"] = [
            {
                "id": message.id,
                "role": message.role,
                "content": redact_sensitive_text(message.content),
                "plan": redact_sensitive_data(parse_json_object_text(message.plan_json)),
                "created_at": message.created_at.isoformat() if message.created_at else None,
            }
            for message in messages
        ]
    return payload

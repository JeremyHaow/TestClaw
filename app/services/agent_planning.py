from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import yaml
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.json_utils import parse_llm_json_object
from app.agent.nodes.source_loader import classify_input
from app.config import settings
from app.core.llm_gateway import llm_gateway
from app.core.redaction import redact_sensitive_data, redact_sensitive_text
from app.models.agent_planning import AgentPlan, AgentPlanningMessage, AgentPlanningSession

logger = logging.getLogger(__name__)

PLAN_SESSION_COLLECTING = "collecting"
PLAN_SESSION_READY = "ready"
PLAN_SESSION_EXECUTED = "executed"

ALLOWED_TEST_TYPES = {"api", "ui"}
ALLOWED_AUTH_MODES = {"auto", "manual", "none_confirmed"}
ALLOWED_CAPTCHA_MODES = {"none", "static", "dynamic"}
ALLOWED_API_POLICIES = {"safe_read_only", "safe_with_auth", "write_allowed"}
MAX_LLM_QUESTION_OPTION_GROUPS = 2
MAX_FALLBACK_QUESTION_OPTION_GROUPS = 1
PLAN_INTAKE_STEPS = {
    "target_kind": "测试目标",
    "coverage_scope": "覆盖范围",
    "auth_boundary": "登录方式/凭证",
    "safety_boundary": "安全边界",
    "success_criteria": "成功标准",
}

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
    r"(?i)\b(?:token|access_token|api_key|apikey)\b\s*(?:[:=：]|是|为)\s*([^\s,，;；。]+)"
)
_API_INTENT_RE = re.compile(r"(?i)\b(api|openapi|swagger|endpoint)\b|接口")
_API_DIRECT_URL_HINT_RE = re.compile(
    r"(?i)\b(api|endpoint|json|header|headers|body|status\s*code|response)\b|"
    r"接口|响应|状态码|字段|断言"
)
_RESPONSE_FIELD_LIST_RE = re.compile(
    r"(?i)(?:包含|包括|含有|返回|需要包含|应包含|必须包含|include|includes|included|"
    r"contain|contains|has|have|return|returns)\s+"
    r"(?:json\s+|response\s+|body\s+)?"
    r"([A-Za-z_][A-Za-z0-9_.-]*(?:\s*(?:、|,|，|/|and|和|及)\s*"
    r"[A-Za-z_][A-Za-z0-9_.-]*)*)\s*"
    r"(?:字段|键|keys?|fields?)?"
)
_FIELD_LIST_AFTER_LABEL_RE = re.compile(
    r"(?i)(?:字段|键|keys?|fields?)\s*(?:[:：=]|包含|包括|include|includes|contain|contains)?\s*"
    r"([A-Za-z_][A-Za-z0-9_.-]*(?:\s*(?:、|,|，|/|and|和|及)\s*"
    r"[A-Za-z_][A-Za-z0-9_.-]*)*)"
)
_RESPONSE_FIELD_STOPWORDS = {
    "and",
    "body",
    "code",
    "contains",
    "field",
    "fields",
    "header",
    "include",
    "includes",
    "json",
    "key",
    "keys",
    "response",
    "status",
}
_UI_INTENT_RE = re.compile(r"(?i)\b(ui|browser|page|web|login page|screen)\b|页面|浏览器|管理后台|后台")
_NO_AUTH_RE = re.compile(
    r"(?i)\b(no auth|public|login not required)\b|"
    r"无需鉴权|公开|不需要登录|无需登录|不用登录|不需要登陆|无需登陆|不用登陆"
)
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
_UNSUPPORTED_TARGET_OPTION_RE = re.compile(
    r"(?i)\b(?:desktop(?:\s+(?:software|app|application|client))?|"
    r"native(?:\s+(?:app|application|mobile\s+app))?|"
    r"mobile(?:\s+(?:app|application|client))?|"
    r"ios(?:\s+(?:app|application|client))?|"
    r"android(?:\s+(?:app|application|client))?|"
    r"iphone(?:\s+(?:app|application))?|ipad(?:\s+(?:app|application))?|"
    r"windows\s+app|macos\s+app|electron\s+app)\b|"
    r"桌面(?:软件|应用|客户端)|手机\s*(?:App|应用|客户端)|"
    r"移动\s*(?:App|应用|客户端|端)|原生\s*(?:App|应用|客户端)|"
    r"安卓(?:App|应用|客户端)?|苹果(?:App|应用|客户端)?|"
    r"iOS\s*(?:App|应用|客户端)?|Android\s*(?:App|应用|客户端)?|"
    r"桌面端|手机端|原生端|PC\s*客户端|本地客户端",
)
_CUSTOM_CHOICE_RE = re.compile(r"(?i)\b(?:custom|other|something else)\b|补充说明|自定义|其他")
_PLACEHOLDER_CHOICE_RE = re.compile(
    r"稍后补充具体地址|我会补充关于.+具体说明|我会直接粘贴目标\s*URL|"
    r"我会补充这份\s*API\s*文档对应的基础\s*URL",
    re.I,
)
_PUBLIC_AUTH_FREE_DOMAINS = (
    "httpbin.org",
    "postman-echo.com",
    "example.com",
    "example.org",
    "example.net",
    "github.io",
    "httpstat.us",
    "jsonplaceholder.typicode.com",
)
_SUCCESS_CRITERIA_RE = re.compile(
    r"(?i)状态码|返回码|响应码|应该是\s*\d|"
    r"\b(?:status[\s_-]?code|status\s+is|must\s+(?:be|return)|expect(?:s|ed)?\s+\d)\b|"
    r"成功标准|发布阻断|必须返回|应当返回|期望返回|返回\s*\d{3}"
)
_NO_AUTH_KEYWORDS_RE = re.compile(
    r"(?i)无需鉴权|无需登录|公开访问|不需要鉴权|不需要登录|不用登录|不用登陆|"
    r"public\s+access|no\s+auth(?:entication)?|login\s+not\s+required"
)


class PlannerAuthCredentials(BaseModel):
    model_config = ConfigDict(extra="ignore")

    username: str | None = None
    password: str | None = None
    captcha: str | None = None
    csrf: str | None = None


class PlannerAuthAcquireConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    username: str | None = None
    password: str | None = None
    captcha: str | None = None
    csrf: str | None = None
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
    question_options: list[dict[str, Any]] = Field(default_factory=list)
    ready_to_execute: bool = False
    plan: dict[str, Any] | None = None
    run_payload: dict[str, Any] | None = None


class PlannerQuestionChoice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label: str
    message: str
    title: str | None = None
    description: str | None = None
    field: str | None = None
    value: str | None = None
    step: str | None = None
    allows_defer: bool = True
    allows_skip: bool = False
    optional: bool = False


class PlannerQuestionOptions(BaseModel):
    model_config = ConfigDict(extra="ignore")

    question: str
    step: str | None = None
    required: bool = True
    options: list[PlannerQuestionChoice] = Field(default_factory=list)


class PlannerTurnResult(BaseModel):
    message: str
    status: str
    questions: list[str] = Field(default_factory=list)
    question_options: list[dict[str, Any]] = Field(default_factory=list)
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


# Sentence terminators include both ASCII and Chinese punctuation; the
# regex captures the trailing terminator so we can preserve it when rebuilding.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?\.;；])\s+|\n+")


def _dedupe_sentences(value: Any, *, limit: int = 800) -> str:
    """Deduplicate sentences case-insensitively while preserving original order.

    Prevents `task_objective` (composed from asset handoff context + user
    free-chat + safety boundary defaults) from collapsing the same
    "安全边界：..." sentence multiple times into the executed plan view.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    sentences = [piece.strip() for piece in _SENTENCE_SPLIT_RE.split(text) if piece and piece.strip()]
    if not sentences:
        return _clean_text(text, limit=limit)
    seen: set[str] = set()
    kept: list[str] = []
    for sentence in sentences:
        normalized = " ".join(sentence.lower().split())
        if normalized in seen:
            continue
        seen.add(normalized)
        kept.append(sentence)
    rebuilt = " ".join(kept)
    return _clean_text(rebuilt, limit=limit)


def _clean_url(value: Any) -> str | None:
    text = str(value or "").strip().strip(".,;，；。")
    if text.startswith(("http://", "https://")):
        return text[:1000]
    return None


def _latest_url(text: str) -> str | None:
    matches = list(_URL_RE.finditer(text))
    return _clean_url(matches[-1].group(0)) if matches else None


def _urls_from_text(text: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for match in _URL_RE.finditer(text):
        url = _clean_url(match.group(0))
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def _response_field_names_from_text(text: str) -> list[str]:
    fields: list[str] = []
    seen: set[str] = set()
    for pattern in (_RESPONSE_FIELD_LIST_RE, _FIELD_LIST_AFTER_LABEL_RE):
        for match in pattern.finditer(text):
            raw = match.group(1)
            for item in re.split(r"\s*(?:、|,|，|/|\band\b|和|及)\s*", raw):
                field_name = item.strip().strip("`'\".。；;:：")
                if (
                    not field_name
                    or field_name.lower() in _RESPONSE_FIELD_STOPWORDS
                    or field_name.lower() in seen
                ):
                    continue
                seen.add(field_name.lower())
                fields.append(field_name)
    return fields[:12]


def _direct_url_response_schema(text: str) -> dict[str, Any]:
    fields = _response_field_names_from_text(text)
    schema: dict[str, Any] = {"type": "object"}
    if fields:
        schema["properties"] = {field: {} for field in fields}
        schema["required"] = fields
        schema["x-testclaw-user-required-fields"] = True
    return schema


def _direct_api_urls_as_openapi_source(text: str) -> str | None:
    if not _API_DIRECT_URL_HINT_RE.search(text):
        return None
    urls = _urls_from_text(text)
    if len(urls) < 2:
        return None

    parsed_urls = [urlsplit(url) for url in urls]
    origins = {f"{item.scheme}://{item.netloc}" for item in parsed_urls if item.scheme and item.netloc}
    if len(origins) != 1:
        return None
    origin = origins.pop().rstrip("/")

    paths: dict[str, Any] = {}
    response_schema = _direct_url_response_schema(text)
    for item in parsed_urls:
        path = item.path or "/"
        parameters = [
            {
                "name": key,
                "in": "query",
                "required": False,
                "schema": {"type": "string"},
                "example": value,
            }
            for key, value in parse_qsl(item.query, keep_blank_values=True)
        ]
        paths[path] = {
            "get": {
                "summary": f"Direct GET {path}",
                "parameters": parameters,
                "responses": {
                    "200": {
                        "description": "Expected successful response",
                        "content": {"application/json": {"schema": response_schema}},
                    }
                },
            }
        }

    return _json_dumps(
        {
            "openapi": "3.0.0",
            "info": {"title": "Direct API URL targets", "version": "1.0.0"},
            "servers": [{"url": origin}],
            "paths": paths,
        }
    )


def _source_from_text(text: str) -> str | None:
    openapi_source = _extract_openapi_source(text)
    if openapi_source:
        return openapi_source[:20000]
    direct_api_source = _direct_api_urls_as_openapi_source(text)
    if direct_api_source:
        return direct_api_source[:20000]
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


def _active_user_text_after_rejection(messages: list[AgentPlanningMessage]) -> tuple[str, bool]:
    start_index = 0
    for index, message in enumerate(messages):
        if message.role == "system" and (
            "计划已拒绝" in message.content or "Plan rejected" in message.content
        ):
            start_index = index + 1
    active_messages = [
        message.content for message in messages[start_index:] if message.role == "user"
    ]
    return "\n".join(active_messages), start_index > 0


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


def _has_public_no_auth_domain(text: str) -> bool:
    """Detect public domains that should infer no-auth without re-asking the user."""
    for url in _urls_from_text(text):
        try:
            host = urlsplit(url).hostname or ""
        except Exception:
            continue
        host = host.lower()
        for domain in _PUBLIC_AUTH_FREE_DOMAINS:
            if host == domain or host.endswith(f".{domain}"):
                return True
    return False


def _recognize_provided_fields(messages: list[AgentPlanningMessage]) -> dict[str, bool]:
    """Identify which planner intake fields are already supplied by user history.

    Used to filter `_missing_questions` so the planner does not re-ask for
    facts already in the conversation. Returns a dict of booleans keyed by
    `target`, `auth_boundary`, `success_criteria` so callers can subtract
    those questions before emitting a generic clarifying response.
    """
    active_text, _ = _active_user_text_after_rejection(messages)
    text = active_text or "\n".join(
        message.content for message in messages if message.role == "user"
    )
    if not text:
        return {"target": False, "auth_boundary": False, "success_criteria": False}
    source = _source_from_text(text)
    target_present = bool(source)
    auth_boundary_present = bool(
        _NO_AUTH_RE.search(text)
        or _NO_AUTH_KEYWORDS_RE.search(text)
        or _has_public_no_auth_domain(text)
    )
    success_criteria_present = bool(_SUCCESS_CRITERIA_RE.search(text))
    return {
        "target": target_present,
        "auth_boundary": auth_boundary_present,
        "success_criteria": success_criteria_present,
    }


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
    if _NO_AUTH_RE.search(lowered) or _NO_AUTH_KEYWORDS_RE.search(lowered):
        return "none_confirmed"
    if _has_public_no_auth_domain(text):
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
    active_user_text, has_rejection_boundary = _active_user_text_after_rejection(messages)
    active_context_text = active_user_text or conversation_text
    latest_user_text = _latest_user_text(messages)
    source = _candidate_source(payload, messages, conversation_text)
    source_input_type = classify_input(source) if source else "unknown"
    base_url = _clean_url(payload.get("base_url")) or _extract_base_url_from_openapi_source(source)
    intent_text = latest_user_text or conversation_text
    test_type_text = (
        intent_text
        if _API_INTENT_RE.search(intent_text.lower()) or _UI_INTENT_RE.search(intent_text.lower())
        else conversation_text
    )
    test_type = _infer_test_type(payload, source, test_type_text)
    credentials = _merge_credentials(payload, active_context_text)
    token = _token_from_payload_or_text(payload, active_context_text)
    headers = _headers_from_payload(payload.get("headers"))
    auth_config = _auth_config_from_payload(payload.get("auth_config"), credentials)
    auth_mode = _infer_auth_mode(
        payload,
        token=token,
        headers=headers,
        credentials=credentials,
        text=intent_text,
    )
    objective_text = active_context_text if has_rejection_boundary else payload.get("objective")
    objective = _dedupe_sentences(
        objective_text or redact_sensitive_text(active_context_text),
        limit=500,
    )
    if not objective:
        objective = "对目标执行安全的 TestClaw 智能体检查。"
    setup_instructions = _dedupe_sentences(
        (active_context_text if has_rejection_boundary else payload.get("setup_instructions"))
        or redact_sensitive_text(active_context_text),
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


def _missing_questions(
    payload: PlannerRunPayload,
    recognized: dict[str, bool] | None = None,
) -> list[str]:
    recognized = recognized or {}
    if not payload.source and not recognized.get("target"):
        return [
            "TestClaw 应测试哪个目标？请粘贴 URL 或 OpenAPI/Swagger 来源。",
            "这次希望覆盖哪些范围？例如关键路径、回归范围、接口契约或页面冒烟。",
            "目标是否需要登录、Token、Header 或测试账号？如果不需要，请明确说明无需登录。",
            "安全边界是什么？是否只允许只读检查，还是测试环境允许写入。",
            "什么结果算成功？请描述通过标准、必须覆盖的断言或需要重点发现的问题。",
        ]
    input_type = classify_input(payload.source) if payload.source else "unknown"
    if input_type in {"swagger_json", "swagger_yaml"} and not payload.base_url:
        questions = [
            "执行这份 API 文档时应使用哪个基础 URL？",
            "这次的成功标准是什么？例如必须覆盖的接口、断言或发布阻断条件。",
        ]
        if recognized.get("success_criteria"):
            questions = [questions[0]]
        return questions
    has_auth_material = bool(
        payload.token
        or payload.headers
        or (
            payload.auth_credentials
            and (payload.auth_credentials.username or payload.auth_credentials.password)
        )
        or (payload.auth_config and payload.auth_config.enabled)
    )
    if payload.test_type == "api" and payload.auth_mode == "auto" and not has_auth_material:
        questions = [
            "这个 API 目标是否需要鉴权？如需鉴权请提供测试 Token/Header 或登录凭据；如果可公开访问，请明确说明无需鉴权。",
            "这次 API 运行的成功标准是什么？例如必须覆盖的接口、状态码或发布阻断条件。",
        ]
        if recognized.get("auth_boundary"):
            questions = [questions[1]]
        if recognized.get("success_criteria"):
            questions = [item for item in questions if "成功标准" not in item]
        return questions
    if payload.test_type == "ui" and payload.auth_mode == "auto" and not has_auth_material:
        questions = [
            "这个页面是否需要登录？如需登录请提供测试账号；如果是公开页面，请明确说明无需登录。",
            "UI 检查的安全边界是什么？例如只浏览、不提交表单，或允许在测试环境写入。",
            "这次 UI 运行的成功标准是什么？例如关键页面可达、核心流程无报错或特定断言通过。",
        ]
        if recognized.get("auth_boundary"):
            questions = [item for item in questions if "是否需要登录" not in item]
        if recognized.get("success_criteria"):
            questions = [item for item in questions if "成功标准" not in item]
        return questions
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
        "auth_summary": _plan_auth_summary(payload),
        "blockers": [],
    }


def _choice(
    *,
    label: str,
    message: str,
    title: str | None = None,
    description: str | None = None,
    field: str | None = None,
    value: str | None = None,
    step: str | None = None,
    allows_defer: bool = True,
    allows_skip: bool = False,
    optional: bool = False,
) -> PlannerQuestionChoice:
    clean_label = _clean_text(label, limit=40)
    clean_message = _clean_text(message, limit=300)
    clean_title = _clean_text(title or clean_label, limit=60)
    clean_description = _clean_text(description or clean_message, limit=220)
    normalized_step = _normalize_plan_step(step or field)
    normalized_field = _normalize_plan_step(field) or normalized_step
    return PlannerQuestionChoice(
        label=clean_label,
        message=clean_message,
        title=clean_title,
        description=clean_description,
        field=_clean_text(normalized_field or field or "", limit=60) or None,
        value=_clean_text(value or "", limit=80) or None,
        step=normalized_step,
        allows_defer=allows_defer,
        allows_skip=allows_skip,
        optional=optional,
    )


def _question_options(
    *,
    question: str,
    options: list[PlannerQuestionChoice],
    step: str | None = None,
    required: bool = True,
) -> PlannerQuestionOptions:
    normalized_step = _normalize_plan_step(step) or _infer_plan_step_from_question(question)
    return PlannerQuestionOptions(
        question=_clean_text(question, limit=180),
        step=normalized_step,
        required=required,
        options=options,
    )


def _normalize_plan_step(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    aliases = {
        "target": "target_kind",
        "target_type": "target_kind",
        "source": "target_kind",
        "scope": "coverage_scope",
        "coverage": "coverage_scope",
        "auth": "auth_boundary",
        "login": "auth_boundary",
        "credentials": "auth_boundary",
        "safety": "safety_boundary",
        "policy": "safety_boundary",
        "success": "success_criteria",
        "criteria": "success_criteria",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in PLAN_INTAKE_STEPS else None


def _infer_plan_step_from_question(question: str) -> str | None:
    text = question.strip()
    if "目标" in text or "URL" in text or "OpenAPI" in text or "Swagger" in text:
        return "target_kind"
    if "范围" in text or "覆盖" in text:
        return "coverage_scope"
    if any(marker in text for marker in ("登录", "账号", "Token", "Header", "鉴权", "凭证")):
        return "auth_boundary"
    if "安全" in text or "只读" in text or "写入" in text:
        return "safety_boundary"
    if "成功" in text or "结果" in text or "断言" in text:
        return "success_criteria"
    return None


def _is_unsupported_target_option(label: str, message: str) -> bool:
    return bool(_UNSUPPORTED_TARGET_OPTION_RE.search(f"{label} {message}"))


def _is_placeholder_choice(label: str, message: str) -> bool:
    return bool(_PLACEHOLDER_CHOICE_RE.search(f"{label} {message}"))


def _is_custom_choice(label: str, message: str, value: str | None = None) -> bool:
    return bool(_CUSTOM_CHOICE_RE.search(label) or str(value or "").lower() == "custom")


def _custom_choice_for_question(question: str, step: str | None = None) -> PlannerQuestionChoice:
    normalized_step = _normalize_plan_step(step) or _infer_plan_step_from_question(question)
    step_label = PLAN_INTAKE_STEPS.get(normalized_step or "", "当前问题")
    return _choice(
        label="自定义",
        title="自定义说明",
        description="在补充说明里写明这一项的具体约束、判断标准或例外情况。",
        message=f"{step_label}：自定义，由补充说明提供具体内容。",
        field=normalized_step,
        value="custom",
        step=normalized_step,
    )


def _supported_target_choices() -> list[PlannerQuestionChoice]:
    return [
        _choice(
            label="API / 接口",
            title="API / OpenAPI",
            description="用于接口文档、接口契约、只读接口覆盖或指定接口回归。",
            message="测试目标类型：API / OpenAPI/Swagger 接口来源。",
            field="target_kind",
            value="api_openapi",
            step="target_kind",
        ),
        _choice(
            label="Web UI / 网页",
            title="Web UI 页面",
            description="用于浏览器页面、登录后业务流程、表单和页面可用性检查。",
            message="测试目标类型：浏览器 Web UI 页面。",
            field="target_kind",
            value="web_page",
            step="target_kind",
        ),
        _choice(
            label="自定义",
            title="自定义目标",
            description="用补充说明描述具体目标，但仍限定在 API 或浏览器 Web UI 范围内。",
            message="测试目标类型：自定义 API/Web UI 目标，由补充说明限定。",
            field="target_kind",
            value="custom",
            step="target_kind",
        ),
    ]


def _dedupe_question_options(
    question_options: list[PlannerQuestionOptions],
    *,
    max_groups: int = MAX_LLM_QUESTION_OPTION_GROUPS,
) -> list[dict[str, Any]]:
    seen_questions: set[str] = set()
    seen_options: set[tuple[str, str, str]] = set()
    normalized: list[dict[str, Any]] = []
    for group in question_options:
        question = redact_sensitive_text(group.question).strip()
        if not question or question in seen_questions:
            continue
        group_step = _normalize_plan_step(group.step) or _infer_plan_step_from_question(question)
        choices: list[PlannerQuestionChoice] = []
        removed_unsupported_target = False
        removed_placeholder_choice = False
        for option in group.options:
            label = redact_sensitive_text(option.label).strip()
            message = redact_sensitive_text(option.message).strip()
            if not label or not message:
                continue
            if _is_placeholder_choice(label, message):
                removed_placeholder_choice = True
                continue
            if _is_unsupported_target_option(label, message):
                removed_unsupported_target = True
                continue
            option_key = (question, label, message)
            if option_key in seen_options:
                continue
            seen_options.add(option_key)
            option_step = _normalize_plan_step(option.step or option.field) or group_step
            option_field = _normalize_plan_step(option.field) or option_step
            choices.append(
                PlannerQuestionChoice(
                    label=label,
                    message=message,
                    title=redact_sensitive_text(option.title or label).strip()[:60] or label,
                    description=redact_sensitive_text(option.description or message).strip()[:220]
                    or message,
                    field=_clean_text(option_field or option.field or "", limit=60) or None,
                    value=_clean_text(option.value or "", limit=80) or None,
                    step=option_step,
                    allows_defer=bool(option.allows_defer),
                    allows_skip=bool(option.allows_skip),
                    optional=bool(option.optional),
                )
            )
        if (removed_unsupported_target or removed_placeholder_choice) and not choices and group_step == "target_kind":
            choices.extend(_supported_target_choices())
        if choices:
            custom_choices = [
                choice
                for choice in choices
                if _is_custom_choice(choice.label, choice.message, choice.value)
            ]
            standard_choices = [
                choice
                for choice in choices
                if not _is_custom_choice(choice.label, choice.message, choice.value)
            ]
            if custom_choices:
                choices = standard_choices[:4] + [custom_choices[0]]
            else:
                choices = standard_choices[:4] + [_custom_choice_for_question(question, group_step)]
            seen_questions.add(question)
            normalized.append(
                PlannerQuestionOptions(
                    question=question,
                    step=group_step,
                    required=bool(group.required),
                    options=choices,
                ).model_dump(mode="json", exclude_none=True)
            )
    return normalized[:max_groups]


def _question_options_from_llm(raw_groups: list[dict[str, Any]]) -> list[PlannerQuestionOptions]:
    groups: list[PlannerQuestionOptions] = []
    for raw in raw_groups:
        if not isinstance(raw, dict):
            continue
        question = str(raw.get("question") or "").strip()
        group_step = _normalize_plan_step(str(raw.get("step") or raw.get("field") or ""))
        if not group_step:
            group_step = _infer_plan_step_from_question(question)
        raw_options = raw.get("options")
        if not question or not isinstance(raw_options, list):
            continue
        choices: list[PlannerQuestionChoice] = []
        for option in raw_options:
            if not isinstance(option, dict):
                continue
            label = str(option.get("label") or option.get("title") or "").strip()
            description = str(option.get("description") or "").strip()
            message = str(option.get("message") or option.get("summary") or "").strip()
            value = str(option.get("value") or "").strip()
            option_step = _normalize_plan_step(
                str(option.get("step") or option.get("field") or group_step or "")
            )
            field = str(option.get("field") or option_step or group_step or "").strip()
            if not message and label:
                step_label = PLAN_INTAKE_STEPS.get(option_step or group_step or "", "选择")
                message = f"{step_label}：{label}。{description}".strip("。")
            if not label or not message:
                continue
            choices.append(
                _choice(
                    label=label,
                    title=str(option.get("title") or label).strip(),
                    description=description or message,
                    message=message,
                    field=field,
                    value=value,
                    step=option_step or group_step,
                    allows_defer=bool(option.get("allows_defer", True)),
                    allows_skip=bool(option.get("allows_skip", False)),
                    optional=bool(option.get("optional", False)),
                )
            )
        groups.append(
            _question_options(
                question=question,
                step=group_step,
                required=bool(raw.get("required", True)),
                options=choices,
            )
        )
    return groups


def _fallback_question_options(
    payload: PlannerRunPayload,
    questions: list[str],
) -> list[dict[str, Any]]:
    question_options: list[PlannerQuestionOptions] = []
    if not payload.source:
        question_options.append(
            _question_options(
                question="要先确定哪类测试目标？",
                step="target_kind",
                options=_supported_target_choices(),
            )
        )
    if any("基础 URL" in question for question in questions):
        question_options.append(
            _question_options(
                question="这份 API 文档对应哪个基础 URL？",
                step="target_kind",
                options=[
                    _choice(
                        label="使用文档地址",
                        title="使用文档 servers",
                        description="优先采用 OpenAPI/Swagger 文档里的 servers、host 或 basePath。",
                        message="基础 URL：优先使用接口文档内声明的 servers、host 或 basePath。",
                        field="target_kind",
                        value="document_servers",
                        step="target_kind",
                    ),
                    _choice(
                        label="指定基础 URL",
                        title="指定 API 基础 URL",
                        description="用补充说明提供完整 API 基础 URL，例如测试环境网关地址。",
                        message="基础 URL：使用补充说明中的完整 API 基础 URL。",
                        field="target_kind",
                        value="base_url_override",
                        step="target_kind",
                    )
                ],
            )
        )
    if any("登录" in question or "账号" in question or "Token" in question for question in questions):
        question_options.append(
            _question_options(
                question="目标的登录或鉴权边界是什么？",
                step="auth_boundary",
                options=[
                    _choice(
                        label="无需登录",
                        title="公开访问",
                        description="目标可匿名访问，计划按无需登录或鉴权处理。",
                        message="登录方式/凭证：目标公开访问，无需登录或鉴权。",
                        field="auth_boundary",
                        value="no_auth",
                        step="auth_boundary",
                    ),
                    _choice(
                        label="提供账号",
                        title="登录流程",
                        description="使用测试账号、密码、验证码说明或登录步骤完成浏览器登录。",
                        message="登录方式/凭证：目标需要登录流程和测试账号。",
                        field="auth_boundary",
                        value="login_flow",
                        step="auth_boundary",
                    ),
                    _choice(
                        label="手动鉴权",
                        title="Token / Header",
                        description="使用 Token、Cookie 或 Header 作为 API/UI 访问凭证。",
                        message="登录方式/凭证：使用手动提供的 Token、Cookie 或 Header。",
                        field="auth_boundary",
                        value="manual_auth",
                        step="auth_boundary",
                    ),
                ],
            )
        )
    question_options.extend(
        [
            _question_options(
                question="先按哪个测试范围规划？",
                step="coverage_scope",
                required=False,
                options=[
                    _choice(
                        label="冒烟范围",
                        title="冒烟检查",
                        description="优先覆盖关键入口、基础可用性和发布前阻断风险。",
                        message="覆盖范围：关键路径和基础可用性冒烟检查。",
                        field="coverage_scope",
                        value="smoke",
                        step="coverage_scope",
                        allows_skip=True,
                        optional=True,
                    ),
                    _choice(
                        label="回归范围",
                        title="回归范围",
                        description="覆盖核心流程、主要回归风险和历史问题区域。",
                        message="覆盖范围：核心流程、主要回归风险和历史问题。",
                        field="coverage_scope",
                        value="regression",
                        step="coverage_scope",
                        allows_skip=True,
                        optional=True,
                    ),
                    _choice(
                        label="接口契约",
                        title="接口契约",
                        description="适合 OpenAPI/Swagger 输入，关注文档契约、状态码和响应结构。",
                        message="覆盖范围：接口契约、状态码和响应结构检查。",
                        field="coverage_scope",
                        value="api_contract",
                        step="coverage_scope",
                        allows_skip=True,
                        optional=True,
                    ),
                ],
            ),
            _question_options(
                question="安全边界是什么？",
                step="safety_boundary",
                options=[
                    _choice(
                        label="只读边界",
                        title="只读检查",
                        description="不创建、修改或删除数据；API 默认限制为安全只读方法。",
                        message="安全边界：只做只读检查，不创建、修改或删除数据。",
                        field="safety_boundary",
                        value="safe_read_only",
                        step="safety_boundary",
                    ),
                    _choice(
                        label="测试环境写入",
                        title="允许测试写入",
                        description="仅限测试环境，并在约定范围内创建、修改或删除测试数据。",
                        message="安全边界：测试环境允许在约定范围内写入测试数据。",
                        field="safety_boundary",
                        value="write_allowed",
                        step="safety_boundary",
                    ),
                    _choice(
                        label="鉴权只读",
                        title="带鉴权只读",
                        description="允许携带凭证访问受保护资源，但仍不执行写入动作。",
                        message="安全边界：允许带鉴权只读访问，不执行写入动作。",
                        field="safety_boundary",
                        value="safe_with_auth",
                        step="safety_boundary",
                    ),
                ],
            ),
            _question_options(
                question="结果怎样才算成功？",
                step="success_criteria",
                required=False,
                options=[
                    _choice(
                        label="证据充分",
                        title="证据充分",
                        description="每个覆盖点都需要结果、证据、失败原因或明确跳过原因。",
                        message="成功标准：每个覆盖点都有结果、证据、失败原因或明确跳过原因。",
                        field="success_criteria",
                        value="evidence_complete",
                        step="success_criteria",
                        allows_skip=True,
                        optional=True,
                    ),
                    _choice(
                        label="阻断优先",
                        title="阻断问题优先",
                        description="优先发现发布阻断问题，并给出可复现步骤和证据。",
                        message="成功标准：优先发现发布阻断问题，并提供可复现证据。",
                        field="success_criteria",
                        value="blocking_findings",
                        step="success_criteria",
                        allows_skip=True,
                        optional=True,
                    ),
                ],
            ),
        ]
    )
    return _dedupe_question_options(
        question_options,
        max_groups=MAX_FALLBACK_QUESTION_OPTION_GROUPS,
    )


def _planner_question_options(
    llm_output: PlannerLLMOutput,
    payload: PlannerRunPayload,
    questions: list[str],
) -> list[dict[str, Any]]:
    llm_question_options = _dedupe_question_options(
        _question_options_from_llm(llm_output.question_options),
        max_groups=MAX_LLM_QUESTION_OPTION_GROUPS,
    )
    if llm_question_options:
        return llm_question_options
    return _fallback_question_options(payload, questions)


def _sanitize_question_options_payload(raw_groups: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_groups, list):
        return []
    return _dedupe_question_options(
        _question_options_from_llm(raw_groups),
        max_groups=MAX_LLM_QUESTION_OPTION_GROUPS,
    )


def _sanitize_llm_output(raw: dict[str, Any]) -> PlannerLLMOutput | None:
    if not raw:
        return None
    try:
        return PlannerLLMOutput(**raw)
    except Exception as exc:
        logger.debug("Planner LLM JSON did not match contract: %s", exc)
        return None


def _is_repetition_of_previous_assistant(
    messages: list[AgentPlanningMessage],
    new_message_body: str,
    new_questions: list[str],
) -> bool:
    """Detect when the new generic collecting reply would duplicate the previous turn.

    Triggers only when the new response would emit the same canonical body
    ("还需要补充这些信息") with an identical (or superset) question set as
    the immediately previous assistant message. Returns False when the new
    questions are a strict subset (i.e. the planner has narrowed scope) so
    legitimate forward progress is not blocked.
    """
    if not new_questions:
        return False
    if "还需要补充这些信息" not in new_message_body:
        return False
    previous_assistant: AgentPlanningMessage | None = None
    for message in reversed(messages):
        if message.role == "assistant":
            previous_assistant = message
            break
    if previous_assistant is None:
        return False
    if "还需要补充这些信息" not in (previous_assistant.content or ""):
        return False
    previous_plan = parse_json_object_text(previous_assistant.plan_json)
    previous_questions_raw: list[Any] = []
    if isinstance(previous_plan, dict):
        questions_value = previous_plan.get("questions")
        if isinstance(questions_value, list):
            previous_questions_raw = questions_value
    previous_set = {
        " ".join(str(item or "").split())
        for item in previous_questions_raw
        if str(item or "").strip()
    }
    new_set = {" ".join(question.split()) for question in new_questions if question.strip()}
    if not previous_set or not new_set:
        return False
    # Allow narrowing: if the new set is a strict subset of the previous set,
    # the planner is making progress and we should not block.
    if new_set < previous_set:
        return False
    return new_set == previous_set or new_set > previous_set


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

        await self._append_assistant_turn(db, session=session)
        await db.commit()
        await db.refresh(session)
        return session, await self.list_messages(db, session_id=session.id)

    async def edit_user_message(
        self,
        db: AsyncSession,
        *,
        session: AgentPlanningSession,
        message_id: str,
        content: str,
    ) -> tuple[AgentPlanningSession, list[AgentPlanningMessage]]:
        content = content.strip()
        if not content:
            raise ValueError("content is required")
        messages = await self.list_messages(db, session_id=session.id)
        target_index = self._message_index(messages, message_id)
        if target_index is None:
            raise LookupError("Planning message not found")
        target_message = messages[target_index]
        if target_message.role != "user":
            raise ValueError("Only user messages can be edited")

        target_message.content = content
        for message in messages[target_index + 1 :]:
            await db.delete(message)
        session.current_plan = None
        session.current_run_payload = None
        session.status = PLAN_SESSION_COLLECTING
        session.rejection_reason = None
        session.updated_at = datetime.utcnow()
        await db.flush()

        await self._append_assistant_turn(db, session=session, refresh_title=True)
        await db.commit()
        await db.refresh(session)
        return session, await self.list_messages(db, session_id=session.id)

    async def delete_messages_from(
        self,
        db: AsyncSession,
        *,
        session: AgentPlanningSession,
        message_id: str,
    ) -> tuple[AgentPlanningSession, list[AgentPlanningMessage]]:
        messages = await self.list_messages(db, session_id=session.id)
        target_index = self._message_index(messages, message_id)
        if target_index is None:
            raise LookupError("Planning message not found")
        for message in messages[target_index:]:
            await db.delete(message)
        remaining_messages = messages[:target_index]
        self._restore_session_state_from_messages(session, remaining_messages)
        self._restore_session_title_from_messages(session, remaining_messages)
        session.rejection_reason = None
        session.updated_at = datetime.utcnow()
        await db.commit()
        await db.refresh(session)
        return session, await self.list_messages(db, session_id=session.id)

    async def delete_session(
        self,
        db: AsyncSession,
        *,
        session: AgentPlanningSession,
    ) -> None:
        messages = await self.list_messages(db, session_id=session.id)
        for message in messages:
            await db.delete(message)
        plan_result = await db.execute(
            select(AgentPlan).where(AgentPlan.session_id == session.id)
        )
        for plan in plan_result.scalars():
            await db.delete(plan)
        await db.delete(session)
        await db.commit()

    def _message_index(
        self,
        messages: list[AgentPlanningMessage],
        message_id: str,
    ) -> int | None:
        for index, message in enumerate(messages):
            if message.id == message_id:
                return index
        return None

    def _restore_session_state_from_messages(
        self,
        session: AgentPlanningSession,
        messages: list[AgentPlanningMessage],
    ) -> None:
        session.current_plan = None
        session.current_run_payload = None
        session.status = PLAN_SESSION_COLLECTING
        if not messages or messages[-1].role != "assistant":
            return
        plan_data = parse_json_object_text(messages[-1].plan_json)
        if not plan_data:
            return
        status = str(plan_data.get("status") or PLAN_SESSION_COLLECTING)
        current_plan = plan_data.get("plan") if isinstance(plan_data.get("plan"), dict) else None
        run_payload = (
            plan_data.get("run_payload") if isinstance(plan_data.get("run_payload"), dict) else None
        )
        ready = bool(plan_data.get("ready_to_execute") and current_plan and run_payload)
        session.status = PLAN_SESSION_READY if ready else status
        session.current_plan = _json_dumps(current_plan) if ready else None
        session.current_run_payload = _json_dumps(run_payload) if ready else None

    def _restore_session_title_from_messages(
        self,
        session: AgentPlanningSession,
        messages: list[AgentPlanningMessage],
    ) -> None:
        if not messages:
            session.title = "新计划"
            return
        for message in reversed(messages):
            if message.role != "assistant":
                continue
            plan_data = parse_json_object_text(message.plan_json)
            plan = plan_data.get("plan") if isinstance(plan_data, dict) else None
            if isinstance(plan, dict) and plan.get("target"):
                session.title = _clean_text(str(plan["target"]), limit=80) or "新计划"
                return
        for message in messages:
            if message.role == "user" and message.content.strip():
                session.title = _clean_text(redact_sensitive_text(message.content), limit=80) or "新计划"
                return
        session.title = "新计划"

    async def _append_assistant_turn(
        self,
        db: AsyncSession,
        *,
        session: AgentPlanningSession,
        refresh_title: bool = False,
    ) -> None:
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
                    "question_options": result.question_options,
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
        if refresh_title or session.title in {"New plan", "新计划"}:
            session.title = self._title_from_messages(messages, result)
        session.updated_at = datetime.utcnow()

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
            planner_timeout = max(float(settings.AGENT_PLAN_LLM_TIMEOUT_SECONDS or 12.0), 0.05)
            raw_llm_output = await asyncio.wait_for(
                self._call_planner_llm(db, session=session, messages=messages),
                timeout=planner_timeout,
            )
        except asyncio.TimeoutError:
            logger.info(
                "Planner LLM timed out after %.1fs, using local fallback",
                float(settings.AGENT_PLAN_LLM_TIMEOUT_SECONDS or 12.0),
            )
        except Exception as exc:
            logger.info("Planner LLM unavailable, using local fallback: %s", exc)

        using_local_fallback = raw_llm_output is None
        llm_output = raw_llm_output or PlannerLLMOutput(
            response="请先提供测试目标，我才能准备可执行计划。",
            status=PLAN_SESSION_COLLECTING,
            questions=[],
            ready_to_execute=False,
            run_payload={},
        )
        payload = normalize_planner_run_payload(llm_output.run_payload, messages)
        recognized = _recognize_provided_fields(messages)
        questions = _missing_questions(payload, recognized)
        ready = not questions and bool(payload.source)
        if ready and llm_output.ready_to_execute is False and llm_output.run_payload:
            ready = str(llm_output.status).lower() in {"ready", "ready_to_execute", "plan_ready"}
        if ready:
            plan = _build_basic_plan(payload, llm_output.plan)
            run_payload = payload.model_dump(mode="json", exclude_none=True)
            response = None if using_local_fallback else (llm_output.response or llm_output.message)
            if not response:
                response = (
                    "信息已足够，我已准备好这次运行计划。"
                    "计划会沿用现有运行预检，并默认按已确认的安全边界执行。"
                    "如果目标、范围、鉴权或成功标准需要调整，可以直接修改上一条需求后重新生成。"
                )
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
        question_options = _planner_question_options(llm_output, payload, questions)
        message = llm_output.response or llm_output.message or "还需要补充一点信息。"
        if not llm_output.questions and questions:
            message = "还需要补充这些信息：\n" + "\n".join(
                f"- {question}" for question in questions[:5]
            )
        if _is_repetition_of_previous_assistant(messages, message, questions):
            message = (
                "上一轮的补充信息还没识别到。请使用上方的选项卡选择目标类型/范围/鉴权/安全/成功标准；"
                "或者直接粘贴目标 URL/OpenAPI 文档地址。"
            )
        return PlannerTurnResult(
            message=redact_sensitive_text(message),
            status=PLAN_SESSION_COLLECTING,
            questions=[redact_sensitive_text(question) for question in questions[:5]],
            question_options=question_options,
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
            "Do not include Markdown or hidden reasoning. Ask useful clarifying questions until "
            "the target, testing scope, auth/login boundary, safety boundary, and success criteria "
            "are clear enough for a tester to approve. When ready, emit a plan card and a "
            "run_payload for the existing TestClaw run API. Visible process should be summarized "
            "as observable actions only, never hidden chain-of-thought.\n\n"
            "Required JSON fields: response, status, questions, question_options, "
            "ready_to_execute, plan, run_payload.\n"
            "When asking questions, include generic selectable question_options as an array of "
            "objects with question, optional step, required, and options. Each option has label, "
            "message, and may include title, description, field, value, step, allows_defer, "
            "allows_skip, and optional. Option messages must be concrete selected-answer summaries "
            "for target_kind, coverage_scope, auth_boundary, safety_boundary, or success_criteria. "
            "Do not use placeholder choices like 'I will provide details later' or ask for the same "
            "missing URL as an option message. Do not make product-specific option branches.\n"
            "TestClaw currently supports only API testing and browser-based Web UI testing. "
            "Never offer desktop software, native app, mobile app, iOS app, or Android app as "
            "target type options.\n"
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


def _latest_question_options(messages: list[AgentPlanningMessage] | None) -> list[dict[str, Any]]:
    if not messages:
        return []
    for message in reversed(messages):
        if message.role != "assistant":
            continue
        plan_data = parse_json_object_text(message.plan_json)
        question_options = (
            plan_data.get("question_options") if isinstance(plan_data, dict) else None
        )
        if isinstance(question_options, list):
            return _sanitize_question_options_payload(question_options)
    return []


def _redacted_message_plan(plan_json: str | None) -> dict[str, Any] | None:
    plan_data = parse_json_object_text(plan_json)
    if not plan_data:
        return None
    safe_plan = redact_sensitive_data(plan_data)
    if isinstance(safe_plan, dict) and "question_options" in safe_plan:
        safe_plan["question_options"] = _sanitize_question_options_payload(
            safe_plan.get("question_options")
        )
    return safe_plan


def redacted_plan_session_payload(
    session: AgentPlanningSession,
    messages: list[AgentPlanningMessage] | None = None,
) -> dict[str, Any]:
    current_plan = parse_json_object_text(session.current_plan)
    current_run_payload = parse_json_object_text(session.current_run_payload)
    title = redact_sensitive_text(session.title)
    if title == "New plan":
        title = "新计划"
    if session.status == PLAN_SESSION_EXECUTED:
        current_step = "executed"
    elif session.status == PLAN_SESSION_READY or current_run_payload:
        current_step = "review"
    else:
        current_step = "target"
    payload: dict[str, Any] = {
        "id": session.id,
        "title": title,
        "status": session.status,
        "ready_to_execute": session.status == PLAN_SESSION_READY and bool(current_run_payload),
        "current_step": current_step,
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
                "plan": _redacted_message_plan(message.plan_json),
                "created_at": message.created_at.isoformat() if message.created_at else None,
            }
            for message in messages
        ]
        payload["question_options"] = _latest_question_options(messages)
    return payload

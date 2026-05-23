import asyncio
import json
import logging
import os
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse
from sqlalchemy import func, select

from app.config import settings

from app.agent.progress import determine_final_status, mark_task_cancelled, persist_task_state
from app.core.dependencies import CurrentUser, DbSession
from app.core.redaction import (
    REDACTED_VALUE,
    is_sensitive_header,
    redact_json_text,
    redact_sensitive_data,
    redact_sensitive_text,
)
from app.database import AsyncSessionLocal
from app.models.environment import Environment
from app.models.llm_provider import LLMProvider
from app.models.task import Task, TaskStatus, TestType
from app.models.test_case import TestCase, TestSuite
from app.schemas.task import TaskListItemRead, TaskRead, parse_task_detail
from app.services.api_auth import (
    AuthResolution,
    coerce_auth_config,
    has_auth_like_header,
    merge_token_header,
    normalize_headers,
    resolve_auto_auth_headers,
)
from app.services.task_service import (
    normalize_agent_test_type,
    normalize_task_status,
    normalize_test_type,
    task_service,
)
from app.api.v1.test_cases import _extract_playwright_commands, _extract_request_template, _suite_case_kind
from app.worker.tasks import run_agent_task, run_graph_with_progress

logger = logging.getLogger(__name__)

router = APIRouter()


class AuthAcquireConfig(BaseModel):
    enabled: bool = False
    username: str | None = None
    password: str | None = None
    captcha: str | None = None
    tenant: str | None = None
    login_url: str | None = None
    method: str = "POST"
    content_type: str = "json"  # json or form
    headers: dict[str, Any] | None = None
    body: dict[str, Any] | None = None
    token_path: str | None = None
    header_name: str = "Authorization"
    token_prefix: str = "Bearer"


class RunCreate(BaseModel):
    source: str  # URL, Swagger URL, or Swagger JSON/YAML text
    test_type: str = "auto"  # auto, api, ui
    objective: str = ""  # optional objective description
    base_url: str | None = None  # optional base URL override
    headers: dict | None = None  # optional headers injection
    token: str | None = None  # optional auth token
    auth_config: AuthAcquireConfig | None = None  # optional login-to-token config
    api_execution_policy: str = "safe_read_only"  # safe_read_only, safe_with_auth, write_allowed
    setup_instructions: str = ""  # optional pre-test setup/context instructions
    login_instructions: str = ""  # deprecated alias kept for compatibility


class RunPreflightRequest(BaseModel):
    source: str
    test_type: str = "auto"
    objective: str = ""
    base_url: str | None = None
    headers: dict | None = None
    token: str | None = None
    auth_config: AuthAcquireConfig | None = None
    api_execution_policy: str = "safe_read_only"
    setup_instructions: str = ""


class RunPreflightCheck(BaseModel):
    key: str
    label: str
    status: str
    detail: str
    action: str | None = None


class RunPreflightCorrectionPrompt(BaseModel):
    key: str
    label: str
    status: str
    detail: str
    action: str | None = None


class RunPreflightMissionCounts(BaseModel):
    endpoint_count: int | None = None
    estimated_executable_count: int | None = None
    estimated_skipped_count: int | None = None
    auth_required_count: int | None = None
    flow_step_count: int
    check_count: int
    ready_count: int
    review_count: int
    blocked_count: int


class RunPreflightMissionPreview(BaseModel):
    handoff: str
    readiness: str
    target: str
    input_mode: str
    test_mode: str
    objective: str
    scope: str
    execution_policy: str
    safety_boundary: str
    auth_readiness: str
    counts: RunPreflightMissionCounts
    correction_prompts: list[RunPreflightCorrectionPrompt] = Field(default_factory=list)


class RunTargetMemoryLastRun(BaseModel):
    run_id: str
    status: str
    test_type: str | None = None
    created_at: str | None = None


class RunTargetMemoryTheme(BaseModel):
    theme: str
    category: str
    count: int
    severity: str
    surfaces: list[str] = Field(default_factory=list)
    last_seen: str | None = None
    recommended_action: str


class RunTargetMemoryBlocker(BaseModel):
    category: str
    label: str
    count: int
    detail: str
    last_seen: str | None = None


class RunTargetMemorySuite(BaseModel):
    suite_id: str
    label: str
    case_count: int


class RunTargetMemory(BaseModel):
    target: str
    previous_run_count: int
    target_run_count: int
    host_run_count: int
    last_run: RunTargetMemoryLastRun | None = None
    recurring_failure_themes: list[RunTargetMemoryTheme] = Field(default_factory=list)
    known_blockers: list[RunTargetMemoryBlocker] = Field(default_factory=list)
    reusable_suite_count: int = 0
    reusable_case_count: int = 0
    reusable_suites: list[RunTargetMemorySuite] = Field(default_factory=list)
    suggested_strategy: str
    confidence: str
    confidence_reason: str


class RunPreflightResponse(BaseModel):
    input_type: str
    test_type: str
    target_url: str
    expected_flow: list[str]
    readiness: str
    checks: list[RunPreflightCheck]
    mission_preview: RunPreflightMissionPreview | None = None
    target_memory: RunTargetMemory | None = None
    warnings: list[str] = []
    endpoint_count: int | None = None
    auth_required_count: int | None = None
    estimated_executable_count: int | None = None
    estimated_skipped_count: int | None = None
    api_execution_policy: str = "safe_read_only"
    api_path_prefix_rewrite: dict[str, str] | None = None
    auth_resolved: bool = False
    auth_strategy: str | None = None
    auth_header_name: str | None = None
    auth_error: str | None = None
    auth_missing_inputs: list[str] = Field(default_factory=list)
    auth_next_action: str | None = None
    auth_required_fields: list[str] = Field(default_factory=list)


class RunCaseAssetSelection(BaseModel):
    source: Literal["api_cases", "ui_cases", "test_cases"]
    index: int = Field(ge=0)
    case: dict[str, Any] | None = None


class RunCaseAssetsCreate(BaseModel):
    suite_name: str | None = None
    cases: list[RunCaseAssetSelection] = Field(default_factory=list)


class RunInterventionCreate(BaseModel):
    supplemental_instructions: str = Field(min_length=1, max_length=8000)
    cancel_current: bool = False


class RunCaseAssetSavedCase(BaseModel):
    id: str
    title: str
    category: str
    priority: str
    source: str
    source_index: int
    case_type: str


class RunCaseAssetsResponse(BaseModel):
    suite_id: str
    suite_name: str
    case_ids: list[str]
    cases: list[RunCaseAssetSavedCase]
    total: int


class RunHistoryStatusCounts(BaseModel):
    total: int = 0
    pending: int = 0
    queued: int = 0
    running: int = 0
    succeeded: int = 0
    failed: int = 0
    bug_found: int = 0
    cancelled: int = 0
    active: int = 0
    completed: int = 0
    pass_rate: float = 0
    issue_rate: float = 0
    bug_rate: float = 0


class RunHistoryTrendBucket(BaseModel):
    date: str
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    bug_found: int = 0
    cancelled: int = 0
    active: int = 0


class RunHistoryQualityTrend(BaseModel):
    direction: str
    label: str
    rationale: str
    recent_issue_rate: float | None = None
    previous_issue_rate: float | None = None
    buckets: list[RunHistoryTrendBucket] = Field(default_factory=list)


class RunHistoryAffectedTarget(BaseModel):
    target: str
    run_count: int
    issue_run_count: int
    failed_count: int
    bug_count: int
    last_seen: str | None = None


class RunHistoryAffectedSurface(BaseModel):
    type: str
    name: str
    issue_count: int
    last_seen: str | None = None
    detail: str | None = None


class RunHistoryRecurringTheme(BaseModel):
    theme: str
    category: str
    count: int
    severity: str
    surfaces: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    last_seen: str | None = None
    recommended_action: str


class RunHistoryEvidenceSummary(BaseModel):
    runs_with_evidence: int = 0
    runs_with_api_evidence: int = 0
    runs_with_screenshots: int = 0
    runs_with_tool_calls: int = 0
    runs_with_reproduction: int = 0
    runs_with_scripts: int = 0
    evidence_rate: float = 0
    reproduction_rate: float = 0


class RunHistoryInsightsResponse(BaseModel):
    generated_at: str
    window_days: int
    sample_limit: int
    window_run_count: int
    analyzed_runs: int
    status_counts: RunHistoryStatusCounts
    quality_trend: RunHistoryQualityTrend
    affected_targets: list[RunHistoryAffectedTarget] = Field(default_factory=list)
    affected_surfaces: list[RunHistoryAffectedSurface] = Field(default_factory=list)
    recurring_themes: list[RunHistoryRecurringTheme] = Field(default_factory=list)
    evidence_reproduction: RunHistoryEvidenceSummary
    recommended_next_actions: list[str] = Field(default_factory=list)


class RunTriageExportRunMetadata(BaseModel):
    id: str
    status: str
    test_type: str
    objective: str
    target: str
    created_at: str | None = None


class RunTriageExportSurface(BaseModel):
    type: str
    name: str
    detail: str | None = None


class RunTriageExportEvidenceItem(BaseModel):
    kind: str | None = None
    summary: str
    status_code: Any | None = None
    failure_type: str | None = None


class RunTriageExportFinding(BaseModel):
    title: str
    source: str
    severity: str
    confidence: str
    surface: str
    description: str
    evidence: list[RunTriageExportEvidenceItem] = Field(default_factory=list)
    reproduction_steps: list[str] = Field(default_factory=list)
    next_action: str


class RunTriageExportSavedSuite(BaseModel):
    suite_id: str
    name: str
    case_count: int


class RunTriageExportReusableAssets(BaseModel):
    generated_api_case_count: int = 0
    generated_ui_case_count: int = 0
    generated_legacy_case_count: int = 0
    saved_suite_count: int = 0
    saved_case_count: int = 0
    saved_suites: list[RunTriageExportSavedSuite] = Field(default_factory=list)
    script_available: bool = False
    script_field: str | None = None


class RunTriageExportLinks(BaseModel):
    run_detail_path: str
    run_api_path: str
    export_markdown_path: str
    export_json_path: str


class RunTriageExportResponse(BaseModel):
    export_version: str
    generated_at: str
    run: RunTriageExportRunMetadata
    summary: str
    release_risk: dict[str, str]
    confidence: dict[str, str]
    blocking_count: int
    affected_surfaces: list[RunTriageExportSurface] = Field(default_factory=list)
    evidence_summary: dict[str, int]
    blocking_findings: list[RunTriageExportFinding] = Field(default_factory=list)
    reproduction: dict[str, Any]
    recommended_next_actions: list[str] = Field(default_factory=list)
    reusable_assets: RunTriageExportReusableAssets
    safe_links: RunTriageExportLinks


def _resolve_setup_instructions(payload: RunCreate) -> str | None:
    return (payload.setup_instructions or payload.login_instructions or "").strip() or None


def _resolve_run_target_url(source: str, input_type: str, base_url: str | None = None) -> str:
    if input_type == "url":
        return source
    return (base_url or source).strip()


def _expected_flow_for(input_type: str, test_type: str, has_base_url: bool = False) -> list[str]:
    if test_type == "api":
        return ["识别输入", "解析 API", "生成接口用例", "执行 API 测试", "生成报告"]
    if test_type == "ui":
        return ["识别入口", "准备浏览器上下文", "规划 UI 场景", "执行 UI 测试", "生成报告"]
    if input_type in ("swagger_url", "swagger_json", "swagger_yaml") and not has_base_url:
        return ["识别 Swagger", "解析 API", "生成接口用例", "执行 API 测试", "生成报告"]
    if input_type in ("swagger_url", "swagger_json", "swagger_yaml"):
        return ["识别 Swagger", "解析 API", "执行 API 测试", "如有 UI 入口则继续 UI 测试", "生成报告"]
    return ["识别目标", "准备浏览器上下文", "规划 UI 场景", "执行 UI 测试", "生成报告"]


async def _count_rows(db: DbSession, model: type[Any]) -> int:
    result = await db.execute(select(func.count()).select_from(model))
    return int(result.scalar_one())


async def _count_default_planners(db: DbSession) -> int:
    result = await db.execute(
        select(func.count()).select_from(LLMProvider).where(
            LLMProvider.is_active.is_(True),
            LLMProvider.is_default_planner.is_(True),
        )
    )
    return int(result.scalar_one())


async def _best_effort_endpoint_count(source: str, input_type: str) -> int | None:
    if input_type == "url":
        return None
    try:
        from app.tools.doc_parser import parse_api_document_content

        content = source
        if input_type == "swagger_url":
            async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
                response = await client.get(source)
                response.raise_for_status()
                content = response.text
        endpoints = parse_api_document_content(content)
        return len(endpoints)
    except Exception:
        return None


async def _best_effort_api_profile(source: str, input_type: str, api_execution_policy: str) -> dict[str, Any]:
    if input_type == "url":
        return {
            "endpoint_count": None,
            "auth_required_count": None,
            "estimated_executable_count": None,
            "estimated_skipped_count": None,
            "target_url": None,
            "api_path_prefix_rewrite": None,
        }

    try:
        from app.agent.nodes.api_runner import (
            WRITE_API_METHODS,
            _normalize_api_execution_policy,
            _policy_allows_write,
        )
        from app.agent.nodes.source_loader import (
            _apply_path_prefix_rewrite,
            _extract_base_url,
            _extract_document_base_url,
            _infer_proxy_prefix_rewrite,
        )
        from app.tools.doc_parser import parse_api_document_content

        content = source
        if input_type == "swagger_url":
            async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
                response = await client.get(source)
                response.raise_for_status()
                content = response.text
        endpoints = parse_api_document_content(content)
        prefix_rewrite = None
        if input_type == "swagger_url":
            prefix_rewrite = _infer_proxy_prefix_rewrite(source, endpoints)
            endpoints = _apply_path_prefix_rewrite(endpoints, prefix_rewrite)
        inferred_target_url = (
            _extract_document_base_url(content, source_url=source if input_type == "swagger_url" else None)
            or (_extract_base_url(source) if input_type == "swagger_url" else None)
        )
        policy = _normalize_api_execution_policy(api_execution_policy)
        write_allowed = _policy_allows_write(policy)
        skipped_for_policy = sum(
            1
            for endpoint in endpoints
            if str(endpoint.get("method", "GET")).upper() in WRITE_API_METHODS and not write_allowed
        )
        return {
            "endpoint_count": len(endpoints),
            "auth_required_count": sum(1 for endpoint in endpoints if endpoint.get("auth_required")),
            "estimated_executable_count": max(len(endpoints) - skipped_for_policy, 0),
            "estimated_skipped_count": skipped_for_policy,
            "target_url": inferred_target_url,
            "api_path_prefix_rewrite": prefix_rewrite,
        }
    except Exception:
        return {
            "endpoint_count": None,
            "auth_required_count": None,
            "estimated_executable_count": None,
            "estimated_skipped_count": None,
            "target_url": None,
            "api_path_prefix_rewrite": None,
        }


async def _best_effort_reachability(source: str) -> str:
    if not source.startswith(("http://", "https://")):
        return "skipped"
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            response = await client.get(source)
        if response.status_code < 500:
            return "ready"
        return "warning"
    except Exception:
        return "warning"


async def _redis_broker_reachable(timeout: float) -> bool:
    try:
        from redis.asyncio import Redis

        client = Redis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=timeout,
            socket_timeout=timeout,
        )
        try:
            await client.ping()
        finally:
            await client.aclose()
        return True
    except Exception as exc:
        logger.debug("Redis broker readiness probe failed: %s", exc)
        return False


async def _best_effort_worker_readiness() -> tuple[str, str, str | None]:
    timeout = max(float(settings.PREFLIGHT_WORKER_TIMEOUT_SECONDS), 0.1)
    if not await _redis_broker_reachable(timeout):
        return (
            "warning",
            "未检测到可用 Redis Broker；创建任务失败时会尝试同步回退",
            "启动 Redis 和 Celery Worker 后重新预检",
        )

    def _ping_workers() -> Any:
        from app.worker.celery_app import celery_app

        inspector = celery_app.control.inspect(timeout=timeout)
        return inspector.ping()

    try:
        replies = await asyncio.wait_for(
            asyncio.to_thread(_ping_workers),
            timeout=timeout + 0.25,
        )
    except Exception as exc:
        logger.debug("Worker readiness probe failed: %s", exc)
        return (
            "warning",
            "未检测到活跃 Worker；创建任务失败时会尝试同步回退",
            "启动 Celery Worker 并确认 Redis 可访问",
        )

    if isinstance(replies, dict) and replies:
        return "ready", f"检测到 {len(replies)} 个活跃 Worker", None

    return (
        "warning",
        "未检测到活跃 Worker；创建任务失败时会尝试同步回退",
        "启动 Celery Worker 并确认 Redis 可访问",
    )


async def _prepare_run_auth(
    payload: RunCreate | RunPreflightRequest,
    *,
    source: str,
    input_type: str,
    target_url: str,
) -> tuple[dict[str, str], AuthResolution]:
    headers = normalize_headers(payload.headers)
    merge_token_header(payload.token, headers)
    resolution = await resolve_auto_auth_headers(
        payload.auth_config,
        source=source,
        input_type=input_type,
        target_url=target_url,
    )
    if resolution.ok:
        headers.update(resolution.headers)
    return headers, resolution


def _preflight_readiness(checks: list[RunPreflightCheck]) -> str:
    if any(check.status == "missing" for check in checks):
        return "blocked"
    if any(check.status == "warning" for check in checks):
        return "needs_review"
    return "ready"


def _redact_url_for_preview(value: str) -> str:
    if not value.startswith(("http://", "https://")):
        return value
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname or ""
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        netloc = hostname
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        if not (parsed.username or parsed.password):
            netloc = parsed.netloc
        query = urlencode(
            [
                (key, REDACTED_VALUE if is_sensitive_header(key) else query_value)
                for key, query_value in parse_qsl(parsed.query, keep_blank_values=True)
            ]
        )
        return urlunsplit((parsed.scheme, netloc, parsed.path, query, ""))
    except Exception:
        return value


def _preflight_target_label(source: str, input_type: str, target_url: str) -> str:
    if input_type in ("swagger_json", "swagger_yaml") and target_url == source:
        return "粘贴的 OpenAPI 文档（运行时解析 Base URL）"
    return _redact_url_for_preview(target_url or source)


def _preflight_input_mode_label(input_type: str) -> str:
    return {
        "url": "网页 URL",
        "swagger_url": "Swagger/OpenAPI URL",
        "swagger_json": "Swagger/OpenAPI JSON",
        "swagger_yaml": "Swagger/OpenAPI YAML",
    }.get(input_type, input_type)


def _preflight_test_mode_label(test_type: str) -> str:
    return {
        "auto": "自动编排",
        "api": "API 检查",
        "ui": "UI 巡检",
    }.get(test_type, test_type)


def _preflight_objective_summary(payload: RunPreflightRequest, input_type: str, test_type: str) -> str:
    objective = payload.objective.strip()
    if objective:
        return objective
    if test_type == "api" or input_type in ("swagger_url", "swagger_json", "swagger_yaml"):
        return "验证 API 契约、参数边界、鉴权路径和错误分支。"
    if test_type == "ui":
        return "巡检入口页面、关键导航、表单路径和可见错误。"
    return "由智能体根据输入自动识别 API/UI 路径并生成测试计划。"


def _preflight_scope_summary(
    input_type: str,
    test_type: str,
    endpoint_count: int | None,
    estimated_executable_count: int | None,
    estimated_skipped_count: int | None,
) -> str:
    if test_type == "ui" or (test_type == "auto" and input_type == "url"):
        return "浏览器会从目标入口开始探索 UI 路径，并采集截图与执行证据。"

    endpoint_text = "运行时解析接口范围" if endpoint_count is None else f"文档包含 {endpoint_count} 个端点"
    if estimated_executable_count is None:
        return f"{endpoint_text}，执行前会继续规划可运行用例。"
    skipped_text = f"，策略跳过 {estimated_skipped_count} 个变更接口" if estimated_skipped_count else ""
    return f"{endpoint_text}，预计执行 {estimated_executable_count} 个接口{skipped_text}。"


def _preflight_policy_summary(api_execution_policy: str) -> str:
    if api_execution_policy == "write_allowed":
        return "允许写入/变更请求；仅适合测试环境或明确可回滚的数据。"
    if api_execution_policy == "safe_with_auth":
        return "带鉴权只读；使用凭据执行只读接口，写入/变更接口仍会跳过。"
    return "安全只读；默认跳过 POST/PUT/PATCH/DELETE，避免误改真实数据。"


def _preflight_safety_boundary(payload: RunPreflightRequest, api_execution_policy: str) -> str:
    if payload.setup_instructions.strip():
        return "已提供前置说明/安全边界；预览不展开可能包含凭据的原文。"
    if api_execution_policy == "write_allowed":
        return "未提供额外安全说明；允许写入前建议补充测试账号、可写范围和清理规则。"
    return "未提供额外安全说明；本次主要依赖执行策略限制高风险动作。"


def _preflight_auth_readiness(
    auth_required_count: int | None,
    supplied_auth: bool,
    auth_resolution: AuthResolution,
) -> str:
    header_name = auth_resolution.header_name or "Authorization"
    if auth_required_count:
        if auth_resolution.ok:
            return f"自动获取 Token 已通过；运行时会注入 {header_name}，预览不展示值。"
        if supplied_auth:
            return "已提供 Token/Header；预览不展示任何鉴权值。"
        return f"检测到 {auth_required_count} 个接口需要鉴权；启动前需要补齐 Token/Header 或自动获取信息。"
    if auth_resolution.ok or supplied_auth:
        return "已提供鉴权信息；本次未检测到文档声明强制鉴权，预览不展示值。"
    return "未检测到接口鉴权要求。"


def _default_correction_action(check: RunPreflightCheck) -> str:
    return {
        "provider": "前往系统设置配置模型后重新预检。",
        "planner": "设置默认 Planner 可让测试计划更稳定。",
        "runner": "确认前端/Worker 镜像已安装浏览器工具。",
        "reachability": "确认目标 URL、内网/VPN、Base URL 或代理路径是否正确。",
        "environment": "可以继续使用当前输入；建议后续保存为环境资产复用。",
        "auth": "补齐 Token/Header，或选择自动获取 Token 并填写登录信息。",
        "api_policy": "确认目标为测试环境后再允许写入/变更接口。",
        "source": "修正目标入口/API 文档，或切换测试模式后重新预检。",
        "worker": "启动 Redis 和 Celery Worker 后重新预检。",
    }.get(check.key, "确认该项后重新预检。")


def _preflight_correction_prompts(
    checks: list[RunPreflightCheck],
    warnings: list[str],
) -> list[RunPreflightCorrectionPrompt]:
    prompts = [
        RunPreflightCorrectionPrompt(
            key=check.key,
            label=check.label,
            status=check.status,
            detail=check.detail,
            action=check.action or _default_correction_action(check),
        )
        for check in checks
        if check.status in {"missing", "warning"}
    ]
    prompts.extend(
        RunPreflightCorrectionPrompt(
            key=f"warning_{index}",
            label="待确认提示",
            status="warning",
            detail=warning,
            action="确认无误后可继续，或回到对应输入修正。",
        )
        for index, warning in enumerate(warnings, start=1)
    )
    return prompts


def _build_mission_preview(
    payload: RunPreflightRequest,
    *,
    source: str,
    input_type: str,
    test_type: str,
    target_url: str,
    expected_flow: list[str],
    readiness: str,
    checks: list[RunPreflightCheck],
    warnings: list[str],
    endpoint_count: int | None,
    auth_required_count: int | None,
    estimated_executable_count: int | None,
    estimated_skipped_count: int | None,
    api_execution_policy: str,
    supplied_auth: bool,
    auth_resolution: AuthResolution,
) -> RunPreflightMissionPreview:
    ready_count = sum(1 for check in checks if check.status == "ready")
    blocked_count = sum(1 for check in checks if check.status == "missing")
    review_count = sum(1 for check in checks if check.status == "warning") + len(warnings)
    handoff = {
        "ready": "预检完成：智能体可以接收本次测试任务。",
        "blocked": "预检发现阻断项：修正后再启动测试智能体。",
    }.get(readiness, "预检完成但有待确认项：确认后可启动测试智能体。")
    return RunPreflightMissionPreview(
        handoff=handoff,
        readiness=readiness,
        target=_preflight_target_label(source, input_type, target_url),
        input_mode=_preflight_input_mode_label(input_type),
        test_mode=_preflight_test_mode_label(test_type),
        objective=_preflight_objective_summary(payload, input_type, test_type),
        scope=_preflight_scope_summary(
            input_type,
            test_type,
            endpoint_count,
            estimated_executable_count,
            estimated_skipped_count,
        ),
        execution_policy=_preflight_policy_summary(api_execution_policy),
        safety_boundary=_preflight_safety_boundary(payload, api_execution_policy),
        auth_readiness=_preflight_auth_readiness(auth_required_count, supplied_auth, auth_resolution),
        counts=RunPreflightMissionCounts(
            endpoint_count=endpoint_count,
            estimated_executable_count=estimated_executable_count,
            estimated_skipped_count=estimated_skipped_count,
            auth_required_count=auth_required_count,
            flow_step_count=len(expected_flow),
            check_count=len(checks),
            ready_count=ready_count,
            review_count=review_count,
            blocked_count=blocked_count,
        ),
        correction_prompts=_preflight_correction_prompts(checks, warnings),
    )


_TARGET_MEMORY_SAMPLE_LIMIT = 100
_TARGET_MEMORY_URL_RE = re.compile(r"https?://[^\s<>\")\]]+")


def _redact_url_for_memory(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except Exception:
        return redact_sensitive_text(value).split("?", 1)[0]
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return redact_sensitive_text(value).split("?", 1)[0]
    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    path = redact_sensitive_text(parsed.path or "")
    return urlunsplit((parsed.scheme, netloc, path, "", ""))


def _target_memory_text(value: Any, limit: int = 220) -> str:
    text = redact_sensitive_text(str(value or "").strip())
    text = _TARGET_MEMORY_URL_RE.sub(lambda match: _redact_url_for_memory(match.group(0)), text)
    text = re.sub(
        r"([?&][A-Za-z0-9_.:-]+)=([^\s&#,;)}\]]+)",
        lambda match: f"{match.group(1)}={REDACTED_VALUE}",
        text,
    )
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}..."


def _target_memory_url_parts(value: Any) -> dict[str, str | None]:
    text = str(value or "").strip()
    if not text:
        return {"host": None, "exact": None, "label": "Unknown target"}
    try:
        parsed = urlsplit(text)
    except Exception:
        safe_text = _target_memory_text(text, 160)
        return {"host": None, "exact": safe_text.lower() if safe_text else None, "label": safe_text or "Unknown target"}
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        hostname = parsed.hostname or ""
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        host = hostname.lower()
        if parsed.port:
            host = f"{host}:{parsed.port}"
        path = (parsed.path or "/").rstrip("/") or "/"
        return {
            "host": host,
            "exact": f"{parsed.scheme.lower()}://{host}{path}",
            "label": _redact_url_for_memory(text),
        }
    safe_text = _target_memory_text(text, 160)
    return {"host": None, "exact": safe_text.lower() if safe_text else None, "label": safe_text or "Unknown target"}


def _target_memory_task_matches(target_parts: dict[str, str | None], task: Task) -> bool:
    task_parts = _target_memory_url_parts(getattr(task, "target_url", None))
    if target_parts.get("host") and task_parts.get("host"):
        return target_parts["host"] == task_parts["host"]
    if target_parts.get("exact") and task_parts.get("exact"):
        return target_parts["exact"] == task_parts["exact"]
    return False


def _target_memory_last_seen(current: datetime | None, candidate: datetime) -> datetime:
    if current is None or candidate > current:
        return candidate
    return current


def _target_memory_add_blocker(
    stats: dict[str, dict[str, Any]],
    *,
    category: str,
    label: str,
    reason: str,
    created_at: datetime,
) -> None:
    detail = _target_memory_text(reason, 220)
    if not detail:
        return
    entry = stats.setdefault(
        category,
        {
            "category": category,
            "label": label,
            "count": 0,
            "detail": detail,
            "last_seen_dt": None,
        },
    )
    entry["count"] += 1
    entry["last_seen_dt"] = _target_memory_last_seen(entry["last_seen_dt"], created_at)
    if entry["detail"] == detail or created_at >= entry["last_seen_dt"]:
        entry["detail"] = detail


def _target_memory_confidence(
    *,
    previous_run_count: int,
    recurring_theme_count: int,
    known_blocker_count: int,
    reusable_suite_count: int,
) -> tuple[str, str]:
    if previous_run_count == 0:
        return "low", "当前目标还没有可复用的历史运行。"
    if previous_run_count >= 3 or recurring_theme_count or reusable_suite_count:
        return "high", "已有多次历史运行、复用资产或重复问题可指导本次策略。"
    if known_blocker_count:
        return "medium", "历史样本有限，但已有可参考的登录、鉴权或前置阻塞。"
    return "medium", "已有少量历史运行，可作为弱记忆参考。"


def _target_memory_strategy(
    *,
    previous_run_count: int,
    last_status: str | None,
    recurring_themes: list[RunTargetMemoryTheme],
    known_blockers: list[RunTargetMemoryBlocker],
    reusable_suite_count: int,
) -> str:
    if previous_run_count == 0:
        return "暂无目标历史；先执行安全冒烟和只读检查，并在完成后保存可复用用例。"
    if known_blockers:
        return f"先确认「{known_blockers[0].label}」已处理，再启动本次运行；必要时补充账号、Token、环境和成功判断。"
    if recurring_themes:
        return f"优先回归反复出现的「{recurring_themes[0].theme}」，并保留证据用于缺陷流转。"
    if reusable_suite_count:
        return "优先复用已保存套件覆盖稳定路径，再用本次目标补充探索性检查。"
    if last_status in {"failed", "bug_found"}:
        return "上次运行未通过；从失败路径和受影响页面/接口开始重跑，再扩展到冒烟范围。"
    if last_status == "succeeded":
        return "沿用上次通过的安全策略，增加少量边界与回归检查以确认无退化。"
    return "结合历史状态先做小范围验证，再根据预检结果扩展覆盖面。"


async def _build_preflight_target_memory(
    db: DbSession,
    *,
    source: str,
    input_type: str,
    target_url: str,
) -> RunTargetMemory:
    target_parts = _target_memory_url_parts(target_url)
    result = await db.execute(select(Task).order_by(Task.created_at.desc()).limit(_TARGET_MEMORY_SAMPLE_LIMIT))
    sampled_tasks = list(result.scalars())
    matching_tasks = [task for task in sampled_tasks if _target_memory_task_matches(target_parts, task)]
    now = datetime.utcnow()
    matching_tasks.sort(key=lambda item: _history_created_at(item, now), reverse=True)

    task_ids = [task.id for task in matching_tasks]
    reusable_suites: list[RunTargetMemorySuite] = []
    reusable_case_count = 0
    if task_ids:
        suite_result = await db.execute(
            select(TestSuite)
            .where(TestSuite.task_id.in_(task_ids))
            .order_by(TestSuite.created_at.desc())
            .limit(10)
        )
        for suite in suite_result.scalars():
            case_ids = suite.test_case_ids if isinstance(suite.test_case_ids, list) else []
            case_count = len(case_ids)
            reusable_case_count += case_count
            reusable_suites.append(
                RunTargetMemorySuite(
                    suite_id=suite.id,
                    label=_target_memory_text(suite.name, 80) or "Untitled suite",
                    case_count=case_count,
                )
            )

    theme_stats: dict[str, dict[str, Any]] = {}
    blocker_stats: dict[str, dict[str, Any]] = {}
    for task in matching_tasks:
        status = _history_status(task)
        created_at = _history_created_at(task, now)
        parsed = redact_sensitive_data(_parse_execution_log_dict(getattr(task, "execution_log", None)))
        triage = _build_run_triage_summary(status, parsed)

        setup_blocked, setup_reason = _setup_intervention_signal(parsed)
        api_blocked, api_reason = _api_intervention_signal(parsed)
        ui_blocked, ui_reason = _ui_intervention_signal(parsed)
        if setup_blocked or ui_blocked:
            _target_memory_add_blocker(
                blocker_stats,
                category="setup_auth",
                label="登录/前置阻塞",
                reason=setup_reason or ui_reason,
                created_at=created_at,
            )
        if api_blocked:
            _target_memory_add_blocker(
                blocker_stats,
                category="api_auth",
                label="API 鉴权阻塞",
                reason=api_reason,
                created_at=created_at,
            )

        issue_run = status in _HISTORY_ISSUE_STATUSES or _triage_int(triage.get("blocking_count")) > 0
        if not issue_run:
            continue
        for finding in _triage_list(triage.get("blocking_findings")):
            if not isinstance(finding, dict):
                continue
            title = _target_memory_text(finding.get("title") or finding.get("description"), 180)
            if not title:
                continue
            category = _history_theme_category(finding)
            theme_key = f"{category}:{_history_normalize_theme(title)}"
            severity = _triage_severity(finding.get("severity"))
            entry = theme_stats.setdefault(
                theme_key,
                {
                    "theme": title,
                    "category": category,
                    "count": 0,
                    "severity": severity,
                    "severity_rank": _triage_severity_rank(severity),
                    "surfaces": set(),
                    "last_seen_dt": None,
                },
            )
            entry["count"] += 1
            entry["last_seen_dt"] = _target_memory_last_seen(entry["last_seen_dt"], created_at)
            if _triage_severity_rank(severity) > entry["severity_rank"]:
                entry["severity"] = severity
                entry["severity_rank"] = _triage_severity_rank(severity)
            surface = _target_memory_text(finding.get("surface"), 120)
            if surface:
                entry["surfaces"].add(surface)

    recurring_themes = [
        RunTargetMemoryTheme(
            theme=item["theme"],
            category=item["category"],
            count=item["count"],
            severity=item["severity"],
            surfaces=sorted(item["surfaces"])[:5],
            last_seen=_history_iso(item["last_seen_dt"]),
            recommended_action=_history_theme_action(item["category"], item["severity"], item["theme"]),
        )
        for item in theme_stats.values()
        if item["count"] > 1
    ]
    recurring_themes.sort(
        key=lambda item: (item.count, _triage_severity_rank(item.severity), item.last_seen or ""),
        reverse=True,
    )

    known_blockers = [
        RunTargetMemoryBlocker(
            category=item["category"],
            label=item["label"],
            count=item["count"],
            detail=item["detail"],
            last_seen=_history_iso(item["last_seen_dt"]),
        )
        for item in blocker_stats.values()
    ]
    known_blockers.sort(key=lambda item: (item.count, item.last_seen or ""), reverse=True)

    target_run_count = sum(
        1
        for task in matching_tasks
        if target_parts.get("exact")
        and _target_memory_url_parts(getattr(task, "target_url", None)).get("exact") == target_parts.get("exact")
    )
    host_run_count = sum(
        1
        for task in matching_tasks
        if target_parts.get("host")
        and _target_memory_url_parts(getattr(task, "target_url", None)).get("host") == target_parts.get("host")
    )
    previous_run_count = host_run_count if target_parts.get("host") else target_run_count

    last_task = matching_tasks[0] if matching_tasks else None
    last_status = _history_status(last_task) if last_task else None
    confidence, confidence_reason = _target_memory_confidence(
        previous_run_count=previous_run_count,
        recurring_theme_count=len(recurring_themes),
        known_blocker_count=len(known_blockers),
        reusable_suite_count=len(reusable_suites),
    )
    target_label = _target_memory_text(_preflight_target_label(source, input_type, target_url), 180)
    return RunTargetMemory(
        target=target_label or str(target_parts.get("label") or "Unknown target"),
        previous_run_count=previous_run_count,
        target_run_count=target_run_count,
        host_run_count=host_run_count,
        last_run=RunTargetMemoryLastRun(
            run_id=last_task.id,
            status=last_status or "",
            test_type=normalize_agent_test_type(last_task.test_type, default="auto") if last_task else None,
            created_at=_history_iso(_history_created_at(last_task, now)) if last_task else None,
        )
        if last_task
        else None,
        recurring_failure_themes=recurring_themes[:5],
        known_blockers=known_blockers[:5],
        reusable_suite_count=len(reusable_suites),
        reusable_case_count=reusable_case_count,
        reusable_suites=reusable_suites[:5],
        suggested_strategy=_target_memory_strategy(
            previous_run_count=previous_run_count,
            last_status=last_status,
            recurring_themes=recurring_themes,
            known_blockers=known_blockers,
            reusable_suite_count=len(reusable_suites),
        ),
        confidence=confidence,
        confidence_reason=confidence_reason,
    )


def _parse_execution_log_dict(log: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(log or "{}")
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _case_asset_source_cases(parsed: dict[str, Any], source: str) -> list[dict[str, Any]]:
    value = parsed.get(source)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _case_asset_source_case(parsed: dict[str, Any], source: str, index: int) -> dict[str, Any] | None:
    cases = parsed.get(source)
    if not isinstance(cases, list) or index < 0 or index >= len(cases):
        return None
    case = cases[index]
    if not isinstance(case, dict):
        return None
    return dict(case)


def _case_asset_merge_case(original: dict[str, Any], edited: dict[str, Any] | None) -> dict[str, Any]:
    if not edited:
        return dict(original)
    merged = dict(original)
    for key, value in edited.items():
        if value is not None:
            merged[key] = value
    return merged


def _case_asset_text(value: Any, *, default: str = "", limit: int = 255) -> str:
    text = redact_sensitive_text(str(value or "").strip())
    if not text:
        text = default
    return text[:limit]


def _case_asset_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        items = value
    elif isinstance(value, str):
        items = re.split(r"(?:\n+|\d+[\.\、\)\]\s]+)", value)
    elif value is None:
        items = []
    else:
        items = [value]

    normalized: list[str] = []
    for item in items:
        if isinstance(item, dict):
            text = (
                item.get("step")
                or item.get("action")
                or item.get("description")
                or item.get("expected")
                or json.dumps(redact_sensitive_data(item), ensure_ascii=False, default=str)
            )
        else:
            text = item
        safe_text = redact_sensitive_text(str(text or "").strip())
        if safe_text:
            normalized.append(safe_text)
    return normalized


def _case_asset_priority(value: Any) -> str:
    priority = str(value or "P2").upper()
    return priority if priority in {"P0", "P1", "P2", "P3"} else "P2"


def _case_asset_kind(source: str, case: dict[str, Any]) -> str:
    if source == "api_cases":
        return "api"
    if source == "ui_cases":
        return "ui"
    return _suite_case_kind(case)


def _case_asset_category(value: Any, case_type: str) -> str:
    fallback = "api" if case_type == "api" else "ui"
    return _case_asset_text(value, default=fallback, limit=50) or fallback


def _redact_case_url_or_path(value: Any) -> str:
    text = redact_sensitive_text(str(value or "").strip())
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
        netloc = parsed.netloc
        if parsed.username or parsed.password:
            hostname = parsed.hostname or ""
            if ":" in hostname and not hostname.startswith("["):
                hostname = f"[{hostname}]"
            netloc = hostname
            if parsed.port:
                netloc = f"{netloc}:{parsed.port}"
        query = urlencode(
            [
                (key, REDACTED_VALUE if is_sensitive_header(key) else redact_sensitive_text(query_value))
                for key, query_value in parse_qsl(parsed.query, keep_blank_values=True)
            ]
        )
        return urlunsplit((parsed.scheme, netloc, parsed.path, query, parsed.fragment))
    except Exception:
        return text


def _safe_case_asset_headers(headers: Any) -> dict[str, Any]:
    if not isinstance(headers, dict):
        return {}
    safe_headers: dict[str, Any] = {}
    for key, value in headers.items():
        key_text = str(key).strip()
        if not key_text or is_sensitive_header(key_text):
            continue
        safe_headers[key_text] = redact_sensitive_data(value)
    return safe_headers


def _safe_case_asset_request_template(case: dict[str, Any]) -> dict[str, Any]:
    template = _extract_request_template(case)
    if not template:
        return {}

    safe: dict[str, Any] = {}
    for key in ("method", "path", "endpoint", "base_url", "url", "expected_status"):
        value = template.get(key)
        if value is None or value == "":
            continue
        if key in {"path", "endpoint", "base_url", "url"}:
            safe[key] = _redact_case_url_or_path(value)
        else:
            safe[key] = redact_sensitive_text(str(value))

    headers = _safe_case_asset_headers(template.get("headers"))
    if headers:
        safe["headers"] = headers

    for key in ("query_params", "params", "body", "json"):
        if key in template:
            safe[key] = redact_sensitive_data(template.get(key))

    return safe


_SENSITIVE_PLAYWRIGHT_TARGET_HINTS = (
    "auth",
    "password",
    "passwd",
    "token",
    "secret",
    "cookie",
    "authorization",
    "authentication",
    "api-key",
    "api key",
    "credential",
    "session",
    "sid",
    "jwt",
    "csrf",
    "xsrf",
    "captcha",
    "mfa",
    "otp",
    "2fa",
    "verification code",
)


def _redact_playwright_command(command: Any) -> str:
    text = redact_sensitive_text(str(command or "").strip())
    if not text:
        return ""

    lowered = text.lower()
    if lowered.startswith(("fill ", "type ")) and any(hint in lowered for hint in _SENSITIVE_PLAYWRIGHT_TARGET_HINTS):
        if REDACTED_VALUE in text:
            return text
        quoted_value = re.compile(r"([\"'])([^\"']*)(\1)\s*$")
        redacted = quoted_value.sub(lambda match: f"{match.group(1)}{REDACTED_VALUE}{match.group(1)}", text)
        if redacted != text:
            return redacted
        parts = text.rsplit(" ", 1)
        if len(parts) == 2:
            return f"{parts[0]} {REDACTED_VALUE}"
    return text


def _safe_case_asset_playwright_commands(case: dict[str, Any]) -> list[str]:
    commands = []
    for command in _extract_playwright_commands(case):
        safe_command = _redact_playwright_command(command)
        if safe_command:
            commands.append(safe_command)
    return commands


def _safe_case_asset_test_data(
    case: dict[str, Any],
    *,
    case_type: str,
    run_id: str,
    source: str,
    source_index: int,
) -> dict[str, Any]:
    test_data: dict[str, Any] = {
        "case_asset": {
            "version": 1,
            "source_run_id": run_id,
            "source": source,
            "source_index": source_index,
            "case_type": case_type,
        }
    }

    request_template = _safe_case_asset_request_template(case)
    if request_template:
        test_data["request_template"] = request_template

    playwright_commands = _safe_case_asset_playwright_commands(case)
    if playwright_commands:
        test_data["playwright_commands"] = playwright_commands

    raw_test_data = case.get("test_data")
    if isinstance(raw_test_data, dict):
        for key in ("target_url", "url", "base_url"):
            value = raw_test_data.get(key)
            if value:
                test_data[key] = _redact_case_url_or_path(value)

    for key in ("target_url", "url", "base_url"):
        value = case.get(key)
        if value and key not in test_data:
            test_data[key] = _redact_case_url_or_path(value)

    return test_data


def _normalize_case_asset_for_save(
    case: dict[str, Any],
    *,
    run_id: str,
    source: str,
    source_index: int,
) -> dict[str, Any]:
    case_type = _case_asset_kind(source, case)
    title = _case_asset_text(
        case.get("title") or case.get("name") or case.get("case_title"),
        default=f"Generated {case_type.upper()} case {source_index + 1}",
        limit=255,
    )
    expected = _case_asset_text_list(
        case.get("expected")
        or case.get("expected_result")
        or case.get("expected_results")
        or case.get("assertions")
    )
    return {
        "title": title,
        "steps": _case_asset_text_list(case.get("steps") or case.get("actions")),
        "expected": expected,
        "priority": _case_asset_priority(case.get("priority")),
        "category": _case_asset_category(case.get("category") or case.get("case_type") or case_type, case_type),
        "test_data": _safe_case_asset_test_data(
            case,
            case_type=case_type,
            run_id=run_id,
            source=source,
            source_index=source_index,
        ),
        "source": f"run_case_asset:{run_id}:{source}:{source_index}"[:100],
        "case_type": case_type,
    }


def _default_case_asset_suite_name(task: Task) -> str:
    objective = _case_asset_text(task.objective, default="Generated cases", limit=120)
    return f"{objective} - accepted cases"[:255]


def _triage_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _triage_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _triage_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _triage_text(value: Any, limit: int = 280) -> str:
    text = redact_sensitive_text(str(value or "").strip())
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}..."


def _triage_url_path(value: Any) -> str:
    text = _triage_text(value, limit=500)
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
    except Exception:
        return text.split("?", 1)[0]
    if parsed.scheme and parsed.netloc:
        return parsed.path or "/"
    return text.split("?", 1)[0]


def _triage_api_surface(result: dict[str, Any]) -> str:
    method = str(result.get("method") or "").upper()
    path = _triage_url_path(result.get("url") or result.get("endpoint") or result.get("path"))
    if method and path:
        return f"{method} {path}"
    return method or path or "API request"


def _triage_surface_from_text(value: Any) -> str:
    tokens = _triage_text(value, limit=500).replace(":", " ").split()
    for index, token in enumerate(tokens[:-1]):
        method = token.upper()
        if method in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
            return f"{method} {_triage_url_path(tokens[index + 1])}"
    return ""


def _triage_failure_results(api_result: Any) -> list[dict[str, Any]]:
    failed: list[dict[str, Any]] = []
    for result in _triage_list(_triage_dict(api_result).get("results")):
        if not isinstance(result, dict):
            continue
        if result.get("skipped") or result.get("passed") is True:
            continue
        failed.append(result)
    return failed


def _triage_failed_ui_cases(ui_result: Any) -> list[dict[str, Any]]:
    failed: list[dict[str, Any]] = []
    result = _triage_dict(ui_result)
    for case in _triage_list(result.get("cases")):
        if not isinstance(case, dict):
            continue
        status = str(case.get("status") or "").lower()
        if case.get("passed") is True or status in {"passed", "success"}:
            continue
        failed.append(case)
    if failed:
        return failed

    commands = [
        item
        for item in _triage_list(result.get("commands"))
        if isinstance(item, dict)
        and not item.get("skipped")
        and item.get("passed") is not True
        and _triage_int(item.get("status_code")) != 0
    ]
    if commands:
        failed.append(
            {
                "title": "UI command failed",
                "case_index": commands[0].get("case_index"),
                "failed_commands": commands,
                "screenshots": [cmd.get("screenshot") for cmd in commands if cmd.get("screenshot")],
            }
        )
    return failed


def _triage_ui_failure_reason(case: dict[str, Any], command: dict[str, Any]) -> str:
    for key in ("failure_reason", "error", "message"):
        value = case.get(key)
        if value:
            return _triage_text(value)

    for key in ("error", "message", "detail"):
        value = command.get(key)
        if value:
            return _triage_text(value)

    if command.get("stderr") or command.get("stdout"):
        return "浏览器命令失败；标准输出和错误输出已隐藏，请复核截图证据或在受控环境重跑。"

    return "UI case did not complete successfully."


def _triage_screenshot_items(*sources: Any) -> list[Any]:
    screenshots: list[Any] = []
    for source in sources:
        if isinstance(source, list):
            screenshots.extend(source)
    return [item for item in screenshots if item]


def _triage_screenshot_count(artifacts: dict[str, Any], ui_result: dict[str, Any], final_report: dict[str, Any]) -> int:
    markers: set[str] = set()

    def add(value: Any) -> None:
        if not value:
            return
        if isinstance(value, dict):
            marker = str(value.get("path") or value.get("screenshot") or value.get("filename") or value.get("url") or value)
        else:
            marker = str(value)
        markers.add(marker)

    for item in _triage_list(artifacts.get("ui_screenshots")):
        add(item)
    for item in _triage_list(ui_result.get("screenshots")):
        add(item)
    for item in _triage_list(_triage_dict(final_report.get("artifacts")).get("screenshots")):
        add(item)
    for case in [*_triage_list(artifacts.get("ui_case_evidence")), *_triage_list(ui_result.get("cases"))]:
        if not isinstance(case, dict):
            continue
        for item in _triage_list(case.get("screenshot_evidence")) or _triage_list(case.get("screenshots")):
            add(item)

    return len(markers)


def _triage_tool_call_count(artifacts: dict[str, Any], final_report: dict[str, Any], parsed: dict[str, Any]) -> int:
    tool_summary = (
        _triage_dict(parsed.get("tool_summary"))
        or _triage_dict(final_report.get("tool_summary"))
        or _triage_dict(artifacts.get("tool_summary"))
    )
    if tool_summary.get("total") is not None:
        return _triage_int(tool_summary.get("total"))
    return len(_triage_list(parsed.get("tool_calls") or artifacts.get("tool_calls")))


def _triage_severity_rank(severity: Any) -> int:
    return {
        "CRITICAL": 4,
        "HIGH": 3,
        "MEDIUM": 2,
        "LOW": 1,
    }.get(str(severity or "").upper(), 2)


def _triage_severity(value: Any, default: str = "MEDIUM") -> str:
    severity = str(value or default).upper()
    return severity if severity in {"CRITICAL", "HIGH", "MEDIUM", "LOW"} else default


def _triage_confidence(has_report: bool, evidence_count: int, finding_evidence_count: int = 0) -> str:
    if has_report and (finding_evidence_count or evidence_count):
        return "high"
    if has_report or evidence_count:
        return "medium"
    return "low"


def _triage_next_action_for_finding(finding: dict[str, Any]) -> str:
    source = str(finding.get("source") or "")
    severity = str(finding.get("severity") or "").upper()
    if source == "api" and severity in {"CRITICAL", "HIGH"}:
        return "阻断发布，先修复接口错误并重跑受影响端点。"
    if source.startswith("ui"):
        return "复现失败路径，确认页面状态、权限和选择器后重跑 UI 用例。"
    return "先复核证据和失败原因，修复后重跑相关测试。"


def _triage_api_finding(result: dict[str, Any], has_report: bool) -> dict[str, Any]:
    surface = _triage_api_surface(result)
    status_code = result.get("status_code")
    failure_type = str(result.get("failure_type") or "")
    severity = "CRITICAL" if _triage_int(status_code) >= 500 else "HIGH" if _triage_int(status_code) in {0, 401, 403} else "MEDIUM"
    reason = result.get("failure_reason") or result.get("error") or f"API result did not pass for {surface}."
    evidence_summary = f"{surface}"
    if status_code not in (None, ""):
        evidence_summary = f"{evidence_summary} returned HTTP {status_code}"
    finding = {
        "title": _triage_text(result.get("label") or f"API failure: {surface}", 160),
        "source": "api",
        "severity": severity,
        "confidence": _triage_confidence(has_report, 1, 1),
        "surface": surface,
        "description": _triage_text(reason),
        "evidence": [
            {
                "kind": "api_result",
                "summary": _triage_text(evidence_summary, 220),
                "status_code": status_code,
                "failure_type": _triage_text(failure_type, 80) if failure_type else None,
            }
        ],
        "reproduction_steps": [
            "打开本运行的 API 测试页签，定位同名失败请求。",
            f"在相同环境重跑 {surface}，仅使用受控测试凭据，不复制报告中的鉴权值。",
            "对比返回状态、业务状态码和断言结果，确认修复后再重跑本次任务。",
        ],
    }
    finding["next_action"] = _triage_next_action_for_finding(finding)
    return finding


def _triage_ui_finding(case: dict[str, Any], has_report: bool, script_available: bool) -> dict[str, Any]:
    title = _triage_text(case.get("title") or case.get("case_title") or "UI case failed", 180)
    screenshots = _triage_screenshot_items(
        _triage_list(case.get("screenshot_evidence")),
        _triage_list(case.get("screenshots")),
    )
    failed_commands = _triage_list(case.get("failed_commands"))
    command = failed_commands[0] if failed_commands and isinstance(failed_commands[0], dict) else {}
    reason = _triage_ui_failure_reason(case, command)
    steps = [
        "打开本运行的 UI 测试或截图证据页签，查看失败用例和最后一张截图。",
    ]
    if script_available:
        steps.append("打开脚本页签运行可复现 Playwright 脚本，复核失败路径。")
    steps.append("修复页面状态、权限或选择器问题后重跑本次任务。")
    finding = {
        "title": title,
        "source": "ui",
        "severity": "MEDIUM",
        "confidence": _triage_confidence(has_report, len(screenshots), len(screenshots)),
        "surface": title,
        "description": _triage_text(reason),
        "evidence": [
            {
                "kind": "screenshot",
                "summary": f"{len(screenshots)} 张截图证据",
            }
        ] if screenshots else [],
        "reproduction_steps": steps,
    }
    finding["next_action"] = _triage_next_action_for_finding(finding)
    return finding


def _triage_report_finding(
    bug: dict[str, Any],
    *,
    has_report: bool,
    api_failures: list[dict[str, Any]],
    ui_failures: list[dict[str, Any]],
    script_available: bool,
) -> dict[str, Any]:
    source = str(bug.get("source") or "report")
    severity = _triage_severity(bug.get("severity"))
    title = _triage_text(bug.get("title") or "Blocking finding", 180)
    description = _triage_text(bug.get("description") or title)
    matched_api = api_failures[0] if source == "api" and len(api_failures) == 1 else None
    surface = _triage_api_surface(matched_api) if matched_api else _triage_surface_from_text(title) or title
    screenshots = _triage_screenshot_items(
        _triage_list(bug.get("screenshots")),
        [bug.get("screenshot")] if bug.get("screenshot") else [],
    )
    evidence: list[dict[str, Any]] = []
    if matched_api:
        evidence.append(
            {
                "kind": "api_result",
                "summary": _triage_text(f"{surface} returned HTTP {matched_api.get('status_code')}", 220),
                "status_code": matched_api.get("status_code"),
                "failure_type": _triage_text(matched_api.get("failure_type"), 80) if matched_api.get("failure_type") else None,
            }
        )
    if screenshots:
        evidence.append({"kind": "screenshot", "summary": f"{len(screenshots)} 张截图证据"})

    reproduction_steps = [
        "打开本运行报告页签，复核该缺陷的说明和证据。",
    ]
    if matched_api:
        reproduction_steps.append(f"在相同环境重跑 {surface}，确认返回状态和断言结果。")
    elif source.startswith("ui") and script_available:
        reproduction_steps.append("打开脚本页签运行可复现 Playwright 脚本。")
    elif ui_failures:
        reproduction_steps.append("打开 UI 测试或截图证据页签，从失败用例继续复现。")
    reproduction_steps.append("修复后重跑本次任务，并把新结果附到缺陷流转记录。")

    finding = {
        "title": title,
        "source": source,
        "severity": severity,
        "confidence": _triage_confidence(has_report, len(evidence), len(evidence)),
        "surface": surface,
        "description": description,
        "evidence": evidence,
        "reproduction_steps": reproduction_steps,
    }
    finding["next_action"] = _triage_next_action_for_finding(finding)
    return finding


def _triage_release_risk(
    *,
    status: str,
    verdict: str,
    blocking_findings: list[dict[str, Any]],
    evidence_count: int,
) -> dict[str, str]:
    normalized_status = status.lower()
    normalized_verdict = verdict.upper()
    highest = max((_triage_severity_rank(item.get("severity")) for item in blocking_findings), default=0)
    if normalized_status == "cancelled":
        return {
            "level": "unknown",
            "label": "无法判断",
            "rationale": "运行已取消，缺少完整结果证据。",
        }
    if highest >= 3 or normalized_status in {"failed", "bug_found"} or normalized_verdict == "FAIL":
        return {
            "level": "high",
            "label": "高发布风险",
            "rationale": "存在阻断缺陷或运行未通过，需要修复并重跑后再评估发布。",
        }
    if blocking_findings or normalized_verdict in {"PARTIAL", "NOT_EXECUTED"}:
        return {
            "level": "medium",
            "label": "需复核",
            "rationale": "存在失败项、部分执行或证据不足，发布前应完成复核。",
        }
    if normalized_verdict == "PASS" or (normalized_status == "succeeded" and evidence_count):
        return {
            "level": "low",
            "label": "低发布风险",
            "rationale": "已执行检查未发现阻断缺陷。",
        }
    return {
        "level": "unknown",
        "label": "等待结果",
        "rationale": "当前运行尚未形成可用于发布判断的完整报告。",
    }


def _triage_add_surface(
    surfaces: list[dict[str, Any]],
    seen: set[tuple[str, str]],
    surface_type: str,
    name: str,
    detail: str | None = None,
) -> None:
    key = (surface_type, name)
    if not name or key in seen:
        return
    seen.add(key)
    item: dict[str, Any] = {"type": surface_type, "name": name}
    if detail:
        item["detail"] = _triage_text(detail, 180)
    surfaces.append(item)


def _build_run_triage_summary(status: str, parsed: dict[str, Any]) -> dict[str, Any]:
    final_report = _triage_dict(parsed.get("final_report"))
    api_result = _triage_dict(parsed.get("api_execution_result"))
    ui_result = _triage_dict(parsed.get("ui_execution_result"))
    artifacts = _triage_dict(parsed.get("artifacts"))
    has_report = bool(final_report)
    script_available = bool(parsed.get("ui_reproducible_script") or artifacts.get("ui_reproducible_script"))

    api_results = _triage_list(api_result.get("results"))
    api_result_count = len(api_results) or _triage_int(api_result.get("executed") or api_result.get("total"))
    screenshot_count = _triage_screenshot_count(artifacts, ui_result, final_report)
    tool_call_count = _triage_tool_call_count(artifacts, final_report, parsed)
    evidence = {
        "count": api_result_count + screenshot_count + tool_call_count,
        "api_result_count": api_result_count,
        "screenshot_count": screenshot_count,
        "tool_call_count": tool_call_count,
    }

    api_failures = _triage_failure_results(api_result)
    ui_failures = _triage_failed_ui_cases(ui_result)
    blocking_findings: list[dict[str, Any]] = []
    seen_findings: set[str] = set()

    for bug in _triage_list(final_report.get("bugs_found")):
        if not isinstance(bug, dict):
            continue
        finding = _triage_report_finding(
            bug,
            has_report=has_report,
            api_failures=api_failures,
            ui_failures=ui_failures,
            script_available=script_available,
        )
        surface_marker = f"{finding.get('source')}:{finding.get('surface')}"
        marker = f"{finding.get('source')}:{finding.get('surface')}:{finding.get('title')}"
        if marker in seen_findings or surface_marker in seen_findings:
            continue
        seen_findings.add(surface_marker)
        seen_findings.add(marker)
        blocking_findings.append(finding)

    for result in api_failures:
        finding = _triage_api_finding(result, has_report)
        marker = f"api:{finding.get('surface')}"
        if marker in seen_findings:
            continue
        seen_findings.add(marker)
        blocking_findings.append(finding)

    for case in ui_failures:
        finding = _triage_ui_finding(case, has_report, script_available)
        marker = f"ui:{finding.get('surface')}"
        if marker in seen_findings:
            continue
        seen_findings.add(marker)
        blocking_findings.append(finding)

    surfaces: list[dict[str, Any]] = []
    seen_surfaces: set[tuple[str, str]] = set()
    for finding in blocking_findings:
        source = str(finding.get("source") or "")
        surface = str(finding.get("surface") or "")
        surface_type = "api_endpoint" if source == "api" else "ui_page" if source.startswith("ui") else "run"
        _triage_add_surface(
            surfaces,
            seen_surfaces,
            surface_type,
            surface,
            finding.get("description"),
        )

    recommendations = [
        _triage_text(item, 220)
        for item in _triage_list(final_report.get("recommendations"))
        if _triage_text(item, 220)
    ]
    if blocking_findings:
        recommendations.insert(0, "阻断发布直到阻断缺陷修复，并重跑受影响端点或页面。")
    else:
        recommendations.append("未发现阻断缺陷；保留本运行证据作为发布评审附件。")
    deduped_recommendations = list(dict.fromkeys(recommendations))[:6]

    verdict = str(final_report.get("overall_verdict") or "").upper()
    release_risk = _triage_release_risk(
        status=status,
        verdict=verdict,
        blocking_findings=blocking_findings,
        evidence_count=evidence["count"],
    )
    confidence_level = _triage_confidence(has_report, evidence["count"])
    reproduction_steps: list[str] = []
    for finding in blocking_findings[:2]:
        for step in _triage_list(finding.get("reproduction_steps")):
            safe_step = _triage_text(step, 220)
            if safe_step and safe_step not in reproduction_steps:
                reproduction_steps.append(safe_step)
    if not reproduction_steps and script_available:
        reproduction_steps.append("打开脚本页签运行可复现 Playwright 脚本。")

    summary_text = _triage_text(final_report.get("summary"), 360)
    if not summary_text:
        if blocking_findings:
            summary_text = f"本次运行发现 {len(blocking_findings)} 个需要分诊的阻断项。"
        elif evidence["count"]:
            summary_text = "本次运行已有可评审证据，未发现阻断缺陷。"
        else:
            summary_text = "本次运行尚无完整报告或证据。"

    return {
        "summary": summary_text,
        "release_risk": release_risk,
        "blocking_count": len(blocking_findings),
        "blocking_findings": blocking_findings,
        "affected_surfaces": surfaces,
        "evidence": evidence,
        "confidence": {
            "level": confidence_level,
            "rationale": (
                "报告和执行证据均可用。"
                if confidence_level == "high"
                else "证据或报告不完整，建议人工复核。"
            ),
        },
        "recommended_next_actions": deduped_recommendations,
        "reproduction": {
            "available": bool(reproduction_steps),
            "script_available": script_available,
            "script_field": "ui_reproducible_script" if parsed.get("ui_reproducible_script") else "artifacts.ui_reproducible_script" if artifacts.get("ui_reproducible_script") else None,
            "steps": reproduction_steps[:6],
        },
        "triage_flow": [
            {
                "key": "copy_summary",
                "label": "复制分诊摘要",
                "enabled": True,
                "detail": "用于发布评审、缺陷单或交接记录。",
            },
            {
                "key": "review_evidence",
                "label": "复核证据",
                "enabled": evidence["count"] > 0,
                "detail": "查看 API 结果、截图证据和工具调用记录。",
            },
            {
                "key": "reproduce",
                "label": "复现失败路径",
                "enabled": bool(reproduction_steps),
                "detail": "按复现步骤定位阻断项。",
            },
            {
                "key": "rerun_after_fix",
                "label": "修复后重跑",
                "enabled": status.lower() in {"succeeded", "failed", "bug_found", "cancelled"},
                "detail": "修复后从当前运行重新发起验证。",
            },
        ],
    }


_TRIAGE_EXPORT_VERSION = "triage_export.v1"
_TRIAGE_EXPORT_FORMATS = {"markdown", "json"}
_TRIAGE_EXPORT_PATH_QUERY_RE = re.compile(
    r"(?P<prefix>^|[\s(])(?P<path>/[^\s?#<>\")\]]+)\?[^\s<>\")\]]+"
)
_TRIAGE_EXPORT_REQUEST_BODY_RE = re.compile(
    r"(?i)\b(?:request[_ -]?(?:body|payload|json)|body|json)\b\s*[:=]\s*(\{.*?\}|\[.*?\]|[^\s,;]+)"
)
_TRIAGE_EXPORT_QUERY_CONTEXT_RE = re.compile(
    r"(?i)\b(?:url[_ -]?query|query(?:[_ -]?params?)?)\b[^.\n;]*"
)
_TRIAGE_EXPORT_KEY_VALUE_RE = re.compile(
    r"([A-Za-z0-9_.:-]+\s*=\s*)([^\s,;&)}\]]+)"
)
_TRIAGE_EXPORT_CREDENTIAL_LABEL_RE = re.compile(
    rf"(?i)\b(?:authorization|proxy-authorization|cookie|set-cookie|x-api-key|api-key|x-auth|auth|"
    rf"(?:x[-_])?(?:jwt|csrf|xsrf|session(?:[_ -]?id)?|sid))\b\s*[:=]?\s*(?:Bearer|Basic)?"
    rf"(?:\s*{re.escape(REDACTED_VALUE)})+"
)


def _triage_export_text(value: Any, limit: int = 360) -> str:
    text = redact_sensitive_text(str(value or "").strip())
    text = _TARGET_MEMORY_URL_RE.sub(lambda match: _redact_url_for_memory(match.group(0)), text)
    text = _TRIAGE_EXPORT_PATH_QUERY_RE.sub(
        lambda match: f"{match.group('prefix')}{match.group('path')}",
        text,
    )
    text = _TRIAGE_EXPORT_QUERY_CONTEXT_RE.sub(
        lambda match: _TRIAGE_EXPORT_KEY_VALUE_RE.sub(
            lambda key_value: f"{key_value.group(1)}{REDACTED_VALUE}",
            match.group(0),
        ),
        text,
    )
    text = _TRIAGE_EXPORT_REQUEST_BODY_RE.sub("request payload redacted", text)
    text = _TRIAGE_EXPORT_CREDENTIAL_LABEL_RE.sub(f"credential {REDACTED_VALUE}", text)
    text = text.replace("\r", " ").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}..."


def _triage_export_status_code(value: Any) -> int | str | None:
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return value
    safe_text = _triage_export_text(value, 40)
    if not safe_text:
        return None
    try:
        return int(safe_text)
    except ValueError:
        return safe_text


def _triage_export_target(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "Unknown target"
    if text.startswith(("http://", "https://")):
        return _triage_export_text(_redact_url_for_memory(text), 240) or "Unknown target"
    return _triage_export_text(text, 240) or "Unknown target"


def _triage_export_release_risk(value: Any) -> dict[str, str]:
    risk = _triage_dict(value)
    return {
        "level": _triage_export_text(risk.get("level") or "unknown", 40),
        "label": _triage_export_text(risk.get("label") or "Unknown", 120),
        "rationale": _triage_export_text(risk.get("rationale"), 280),
    }


def _triage_export_confidence(value: Any) -> dict[str, str]:
    confidence = _triage_dict(value)
    return {
        "level": _triage_export_text(confidence.get("level") or "unknown", 40),
        "rationale": _triage_export_text(confidence.get("rationale"), 280),
    }


def _triage_export_surfaces(value: Any) -> list[RunTriageExportSurface]:
    surfaces: list[RunTriageExportSurface] = []
    for item in _triage_list(value):
        surface = _triage_dict(item)
        name = _triage_export_text(surface.get("name"), 180)
        if not name:
            continue
        surfaces.append(
            RunTriageExportSurface(
                type=_triage_export_text(surface.get("type") or "target", 80),
                name=name,
                detail=_triage_export_text(surface.get("detail"), 240) or None,
            )
        )
    return surfaces[:12]


def _triage_export_evidence(value: Any) -> list[RunTriageExportEvidenceItem]:
    evidence: list[RunTriageExportEvidenceItem] = []
    for item in _triage_list(value):
        evidence_item = _triage_dict(item)
        summary = _triage_export_text(evidence_item.get("summary") or evidence_item.get("kind"), 240)
        if not summary:
            continue
        evidence.append(
            RunTriageExportEvidenceItem(
                kind=_triage_export_text(evidence_item.get("kind"), 80) or None,
                summary=summary,
                status_code=_triage_export_status_code(evidence_item.get("status_code")),
                failure_type=_triage_export_text(evidence_item.get("failure_type"), 80) or None,
            )
        )
    return evidence[:8]


def _triage_export_findings(value: Any) -> list[RunTriageExportFinding]:
    findings: list[RunTriageExportFinding] = []
    for item in _triage_list(value):
        finding = _triage_dict(item)
        title = _triage_export_text(finding.get("title") or finding.get("description") or "Blocking finding", 180)
        description = _triage_export_text(finding.get("description"), 360)
        findings.append(
            RunTriageExportFinding(
                title=title,
                source=_triage_export_text(finding.get("source") or "report", 60),
                severity=_triage_severity(finding.get("severity")),
                confidence=_triage_export_text(finding.get("confidence") or "medium", 40),
                surface=_triage_export_text(finding.get("surface"), 180),
                description=description,
                evidence=_triage_export_evidence(finding.get("evidence")),
                reproduction_steps=[
                    step
                    for step in (
                        _triage_export_text(raw_step, 280)
                        for raw_step in _triage_list(finding.get("reproduction_steps"))
                    )
                    if step
                ][:6],
                next_action=_triage_export_text(finding.get("next_action"), 240),
            )
        )
    return findings[:12]


def _triage_export_reproduction(value: Any) -> dict[str, Any]:
    reproduction = _triage_dict(value)
    return {
        "available": bool(reproduction.get("available")),
        "script_available": bool(reproduction.get("script_available")),
        "script_field": _triage_export_text(reproduction.get("script_field"), 120) or None,
        "steps": [
            step
            for step in (
                _triage_export_text(raw_step, 280)
                for raw_step in _triage_list(reproduction.get("steps"))
            )
            if step
        ][:8],
    }


def _triage_export_evidence_summary(value: Any) -> dict[str, int]:
    evidence = _triage_dict(value)
    return {
        "count": _triage_int(evidence.get("count")),
        "api_result_count": _triage_int(evidence.get("api_result_count")),
        "screenshot_count": _triage_int(evidence.get("screenshot_count")),
        "tool_call_count": _triage_int(evidence.get("tool_call_count")),
    }


def _triage_export_case_count(parsed: dict[str, Any], key: str) -> int:
    return sum(1 for item in _triage_list(parsed.get(key)) if isinstance(item, dict))


def _triage_export_suite_case_count(suite: TestSuite) -> int:
    case_ids = suite.test_case_ids if isinstance(suite.test_case_ids, list) else []
    return len(case_ids)


async def _triage_export_saved_suites(db: DbSession, run_id: str) -> list[RunTriageExportSavedSuite]:
    result = await db.execute(
        select(TestSuite)
        .where(TestSuite.task_id == run_id)
        .order_by(TestSuite.created_at.desc())
        .limit(20)
    )
    suites = list(result.scalars())
    return [
        RunTriageExportSavedSuite(
            suite_id=suite.id,
            name=_triage_export_text(suite.name, 180) or "Saved suite",
            case_count=_triage_export_suite_case_count(suite),
        )
        for suite in suites
    ]


async def _build_run_triage_export(
    db: DbSession,
    task: Task,
    parsed: dict[str, Any],
    triage_summary: dict[str, Any],
) -> RunTriageExportResponse:
    run_id = str(task.id)
    status = _status_value(task.status)
    reproduction = _triage_export_reproduction(triage_summary.get("reproduction"))
    saved_suites = await _triage_export_saved_suites(db, run_id)
    saved_case_count = sum(suite.case_count for suite in saved_suites)

    return RunTriageExportResponse(
        export_version=_TRIAGE_EXPORT_VERSION,
        generated_at=datetime.utcnow().isoformat(),
        run=RunTriageExportRunMetadata(
            id=run_id,
            status=_triage_export_text(status, 80),
            test_type=_triage_export_text(normalize_agent_test_type(task.test_type, default="auto"), 60),
            objective=_triage_export_text(task.objective, 280),
            target=_triage_export_target(task.target_url),
            created_at=task.created_at.isoformat() if task.created_at else None,
        ),
        summary=_triage_export_text(triage_summary.get("summary"), 500),
        release_risk=_triage_export_release_risk(triage_summary.get("release_risk")),
        confidence=_triage_export_confidence(triage_summary.get("confidence")),
        blocking_count=_triage_int(triage_summary.get("blocking_count")),
        affected_surfaces=_triage_export_surfaces(triage_summary.get("affected_surfaces")),
        evidence_summary=_triage_export_evidence_summary(triage_summary.get("evidence")),
        blocking_findings=_triage_export_findings(triage_summary.get("blocking_findings")),
        reproduction=reproduction,
        recommended_next_actions=[
            action
            for action in (
                _triage_export_text(raw_action, 280)
                for raw_action in _triage_list(triage_summary.get("recommended_next_actions"))
            )
            if action
        ][:8],
        reusable_assets=RunTriageExportReusableAssets(
            generated_api_case_count=_triage_export_case_count(parsed, "api_cases"),
            generated_ui_case_count=_triage_export_case_count(parsed, "ui_cases"),
            generated_legacy_case_count=_triage_export_case_count(parsed, "test_cases"),
            saved_suite_count=len(saved_suites),
            saved_case_count=saved_case_count,
            saved_suites=saved_suites,
            script_available=bool(reproduction.get("script_available")),
            script_field=reproduction.get("script_field"),
        ),
        safe_links=RunTriageExportLinks(
            run_detail_path=f"/runs/{run_id}",
            run_api_path=f"/api/v1/runs/{run_id}",
            export_markdown_path=f"/api/v1/runs/{run_id}/triage-export?format=markdown",
            export_json_path=f"/api/v1/runs/{run_id}/triage-export?format=json",
        ),
    )


def _triage_export_content_disposition(run_id: str, export_format: str) -> str:
    extension = "md" if export_format == "markdown" else "json"
    return f'attachment; filename="testclaw-triage-{run_id[:8]}.{extension}"'


def _markdown_text(value: Any, fallback: str = "") -> str:
    text = _triage_export_text(value, 600).replace("\n", " ").strip()
    return text or fallback


def _render_run_triage_export_markdown(payload: dict[str, Any]) -> str:
    run = _triage_dict(payload.get("run"))
    risk = _triage_dict(payload.get("release_risk"))
    confidence = _triage_dict(payload.get("confidence"))
    evidence = _triage_dict(payload.get("evidence_summary"))
    reproduction = _triage_dict(payload.get("reproduction"))
    assets = _triage_dict(payload.get("reusable_assets"))
    links = _triage_dict(payload.get("safe_links"))
    lines = [
        "# TestClaw Triage Export",
        "",
        "## Run Metadata",
        f"- Run ID: `{_markdown_text(run.get('id'))}`",
        f"- Status: {_markdown_text(run.get('status'), 'unknown')}",
        f"- Test type: {_markdown_text(run.get('test_type'), 'unknown')}",
        f"- Target: {_markdown_text(run.get('target'), 'Unknown target')}",
        f"- Created: {_markdown_text(run.get('created_at'), 'unknown')}",
        f"- Objective: {_markdown_text(run.get('objective'), 'No objective provided')}",
        "",
        "## Release Risk",
        f"- Level: {_markdown_text(risk.get('label'), 'Unknown')} ({_markdown_text(risk.get('level'), 'unknown')})",
        f"- Rationale: {_markdown_text(risk.get('rationale'), 'No rationale available')}",
        f"- Confidence: {_markdown_text(confidence.get('level'), 'unknown')} - {_markdown_text(confidence.get('rationale'), 'No confidence rationale available')}",
        f"- Blocking findings: {_triage_int(payload.get('blocking_count'))}",
        "",
        "## Evidence Summary",
        f"- Total evidence signals: {_triage_int(evidence.get('count'))}",
        f"- API results: {_triage_int(evidence.get('api_result_count'))}",
        f"- Screenshots: {_triage_int(evidence.get('screenshot_count'))}",
        f"- Tool calls: {_triage_int(evidence.get('tool_call_count'))}",
    ]

    surfaces = _triage_list(payload.get("affected_surfaces"))
    lines.extend(["", "## Affected Surfaces"])
    if surfaces:
        for surface in surfaces:
            item = _triage_dict(surface)
            detail = _markdown_text(item.get("detail"))
            suffix = f" - {detail}" if detail else ""
            lines.append(f"- [{_markdown_text(item.get('type'), 'target')}] {_markdown_text(item.get('name'), 'Unknown')}{suffix}")
    else:
        lines.append("- No affected surfaces were identified.")

    lines.extend(["", "## Blocking Findings"])
    findings = _triage_list(payload.get("blocking_findings"))
    if findings:
        for index, finding_value in enumerate(findings, start=1):
            finding = _triage_dict(finding_value)
            lines.extend(
                [
                    "",
                    f"### {index}. {_markdown_text(finding.get('title'), 'Finding')}",
                    f"- Severity: {_markdown_text(finding.get('severity'), 'MEDIUM')}",
                    f"- Confidence: {_markdown_text(finding.get('confidence'), 'medium')}",
                    f"- Surface: {_markdown_text(finding.get('surface'), 'Unknown surface')}",
                    f"- Description: {_markdown_text(finding.get('description'), 'No description available')}",
                ]
            )
            evidence_items = _triage_list(finding.get("evidence"))
            if evidence_items:
                lines.append("- Evidence:")
                for evidence_value in evidence_items:
                    evidence_item = _triage_dict(evidence_value)
                    status_code = evidence_item.get("status_code")
                    status_text = f" (status {status_code})" if status_code not in (None, "") else ""
                    lines.append(f"  - {_markdown_text(evidence_item.get('summary'), 'Evidence')}{status_text}")
            steps = _triage_list(finding.get("reproduction_steps"))
            if steps:
                lines.append("- Reproduction:")
                for step_index, step in enumerate(steps, start=1):
                    lines.append(f"  {step_index}. {_markdown_text(step)}")
            next_action = _markdown_text(finding.get("next_action"))
            if next_action:
                lines.append(f"- Next action: {next_action}")
    else:
        lines.append("- No blocking findings surfaced in this run.")

    lines.extend(["", "## Reproduction Steps"])
    reproduction_steps = _triage_list(reproduction.get("steps"))
    if reproduction_steps:
        for index, step in enumerate(reproduction_steps, start=1):
            lines.append(f"{index}. {_markdown_text(step)}")
    else:
        lines.append("- No failure reproduction path is required for this report.")

    lines.extend(["", "## Recommended Next Actions"])
    actions = _triage_list(payload.get("recommended_next_actions"))
    if actions:
        for action in actions:
            lines.append(f"- {_markdown_text(action)}")
    else:
        lines.append("- No follow-up action was generated.")

    lines.extend(
        [
            "",
            "## Reusable Assets",
            f"- Generated API cases: {_triage_int(assets.get('generated_api_case_count'))}",
            f"- Generated UI cases: {_triage_int(assets.get('generated_ui_case_count'))}",
            f"- Legacy generated cases: {_triage_int(assets.get('generated_legacy_case_count'))}",
            f"- Saved suites: {_triage_int(assets.get('saved_suite_count'))}",
            f"- Saved suite cases: {_triage_int(assets.get('saved_case_count'))}",
            f"- Reproducible script available: {'yes' if assets.get('script_available') else 'no'}",
        ]
    )
    saved_suites = _triage_list(assets.get("saved_suites"))
    if saved_suites:
        lines.append("- Saved suite details:")
        for suite_value in saved_suites:
            suite = _triage_dict(suite_value)
            lines.append(
                f"  - {_markdown_text(suite.get('name'), 'Saved suite')} "
                f"(`{_markdown_text(suite.get('suite_id'))}`), {_triage_int(suite.get('case_count'))} cases"
            )

    lines.extend(
        [
            "",
            "## Safe Links and Identifiers",
            f"- Run detail: {_markdown_text(links.get('run_detail_path'))}",
            f"- Run API detail: {_markdown_text(links.get('run_api_path'))}",
            f"- Markdown export: {_markdown_text(links.get('export_markdown_path'))}",
            f"- JSON export: {_markdown_text(links.get('export_json_path'))}",
        ]
    )

    return "\n".join(lines).strip() + "\n"


_INTERVENTION_ACTIVE_STATUSES = {"queued", "running"}
_INTERVENTION_SOURCE_STATUSES = {"failed", "bug_found", "cancelled", *_INTERVENTION_ACTIVE_STATUSES}
_INTERVENTION_SETUP_TERMS = (
    "login",
    "log in",
    "auth",
    "authenticate",
    "credential",
    "password",
    "captcha",
    "mfa",
    "2fa",
    "token",
    "cookie",
    "session",
    "setup",
    "pre-test",
    "verification failed",
    "unauthorized",
    "forbidden",
    "401",
    "403",
    "登录",
    "鉴权",
    "认证",
    "凭据",
    "密码",
    "验证码",
    "前置",
)
_INTERVENTION_ENV_TERMS = (
    "base url",
    "environment",
    "env",
    "network",
    "vpn",
    "proxy",
    "connect",
    "timeout",
    "reachable",
    "not found",
    "dns",
    "browser",
    "playwright",
    "环境",
    "网络",
    "代理",
    "可达",
)


def _status_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value or "")


def _contains_any_term(value: Any, terms: tuple[str, ...]) -> bool:
    text = str(value or "").lower()
    return any(term in text for term in terms)


def _intervention_reason_text(*values: Any, fallback: str) -> str:
    for value in values:
        text = _triage_text(value, 260)
        if text:
            return text
    return fallback


def _setup_intervention_signal(parsed: dict[str, Any]) -> tuple[bool, str]:
    setup_result = _triage_dict(parsed.get("setup_result") or parsed.get("login_result"))
    login_verified = parsed.get("login_verified")
    status = str(setup_result.get("status") or "").lower()
    reason = (
        setup_result.get("reason")
        or setup_result.get("error")
        or parsed.get("login_verification_reason")
        or parsed.get("last_error")
    )
    required = bool(setup_result.get("required"))
    failed = (
        (required and login_verified is False)
        or setup_result.get("success") is False
        or status in {"failed", "error", "blocked"}
        or _contains_any_term(reason, _INTERVENTION_SETUP_TERMS)
    )
    if not failed:
        return False, ""
    return True, _intervention_reason_text(
        reason,
        parsed.get("last_error"),
        fallback="登录、鉴权或前置准备未通过，需要补充可执行上下文。",
    )


def _api_intervention_signal(parsed: dict[str, Any]) -> tuple[bool, str]:
    api_result = _triage_dict(parsed.get("api_execution_result"))
    for result in _triage_list(api_result.get("results")):
        item = _triage_dict(result)
        status_code = _triage_int(item.get("status_code") or item.get("envelope_status_code"))
        reason = (
            item.get("skip_reason")
            or item.get("failure_reason")
            or item.get("error")
            or item.get("category")
            or item.get("label")
        )
        if item.get("skipped") and _contains_any_term(reason, _INTERVENTION_SETUP_TERMS):
            return True, _intervention_reason_text(
                reason,
                fallback="API 用例因鉴权或上下文不足被跳过，需要补充 Token/Header 或登录信息。",
            )
        if status_code in {401, 403}:
            return True, _intervention_reason_text(
                reason,
                fallback=f"API 返回 {status_code}，需要补充可用鉴权信息后重跑。",
            )

    skipped = _triage_int(api_result.get("skipped"))
    executed = _triage_int(api_result.get("executed") or api_result.get("completed"))
    if skipped and not executed:
        return True, "API 执行全部跳过，通常需要补充鉴权、Base URL、环境或可执行范围说明。"
    return False, ""


def _ui_intervention_signal(parsed: dict[str, Any]) -> tuple[bool, str]:
    ui_result = _triage_dict(parsed.get("ui_execution_result"))
    for case in _triage_list(ui_result.get("cases")):
        item = _triage_dict(case)
        reason = item.get("skip_reason") or item.get("failure_reason") or item.get("error") or item.get("reason")
        status = str(item.get("status") or "").lower()
        if status in {"skipped", "failed", "blocked"} and _contains_any_term(reason, _INTERVENTION_SETUP_TERMS):
            return True, _intervention_reason_text(
                reason,
                fallback="UI 用例因登录、鉴权或前置状态不足未执行，需要补充测试账号和路径说明。",
            )

    total = _triage_int(ui_result.get("total"))
    completed = _triage_int(ui_result.get("completed"))
    failed = _triage_int(ui_result.get("failed"))
    last_error = parsed.get("last_error")
    if (failed or (total and not completed)) and _contains_any_term(last_error, _INTERVENTION_SETUP_TERMS):
        return True, _intervention_reason_text(
            last_error,
            fallback="UI 执行在前置准备或登录阶段受阻，需要补充上下文后重跑。",
        )
    return False, ""


def _build_run_intervention_summary(
    status: str,
    parsed: dict[str, Any],
    triage_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_status = str(status or "").lower()
    triage = _triage_dict(triage_summary)
    last_error = parsed.get("last_error")
    setup_blocked, setup_reason = _setup_intervention_signal(parsed)
    api_blocked, api_reason = _api_intervention_signal(parsed)
    ui_blocked, ui_reason = _ui_intervention_signal(parsed)
    env_blocked = _contains_any_term(last_error, _INTERVENTION_ENV_TERMS)

    category = "none"
    reason = ""
    suggested_inputs: list[str] = []

    if setup_blocked or ui_blocked:
        category = "setup_auth"
        reason = setup_reason or ui_reason
        suggested_inputs = [
            "可用测试账号、密码、租户/角色和验证码处理方式",
            "登录入口、登录后成功标志、需要跳过或批准的安全提示",
            "测试数据准备步骤、允许写入范围和清理规则",
        ]
    elif api_blocked:
        category = "api_auth"
        reason = api_reason
        suggested_inputs = [
            "有效 Token/Header/Cookie 或自动登录接口字段",
            "API Base URL、网关前缀、租户或环境 Header",
            "哪些写入接口允许在测试环境执行",
        ]
    elif env_blocked:
        category = "environment"
        reason = _intervention_reason_text(
            last_error,
            fallback="运行受环境、网络、Base URL 或浏览器执行条件阻塞。",
        )
        suggested_inputs = [
            "正确环境入口、API Base URL、代理/VPN/内网访问说明",
            "需要预置的测试数据、功能开关或账号权限",
            "目标系统不可用时可跳过的范围",
        ]
    elif normalized_status in {"failed", "cancelled"} and last_error:
        category = "run_blocker"
        reason = _intervention_reason_text(
            last_error,
            fallback="运行未完成；可补充上下文后发起辅助重跑。",
        )
        suggested_inputs = [
            "失败前缺失的操作步骤、环境准备或约束",
            "可复用账号、数据、页面路径或 API 前缀",
            "希望本次重跑优先覆盖或跳过的范围",
        ]
    elif normalized_status == "bug_found" and triage.get("blocking_count"):
        category = "triage_followup"
        reason = _intervention_reason_text(
            triage.get("summary"),
            fallback="存在阻断发现；修复或补充约束后可发起辅助重跑。",
        )
        suggested_inputs = [
            "已修复内容、影响面和需要重点回归的路径",
            "新凭据、测试数据或允许执行的变更操作",
            "需要跳过的已知问题或不稳定范围",
        ]

    useful = bool(category != "none" and normalized_status in _INTERVENTION_SOURCE_STATUSES)
    requires_cancel_current = normalized_status in _INTERVENTION_ACTIVE_STATUSES
    if requires_cancel_current and useful:
        recommended_action = "当前运行仍在执行；确认取消当前运行后，提交补充上下文并发起辅助重跑。"
    elif category == "setup_auth":
        recommended_action = "补充测试账号、登录步骤、验证码/租户/角色和成功判断后，发起辅助重跑。"
    elif category == "api_auth":
        recommended_action = "补充 Token/Header 或自动登录所需字段后，发起辅助重跑。"
    elif category == "environment":
        recommended_action = "补充环境、网络、Base URL、代理或数据准备说明后，发起辅助重跑。"
    elif useful:
        recommended_action = "补充缺失上下文、约束或修复说明后，发起辅助重跑。"
    else:
        recommended_action = "当前运行未检测到需要人工补充上下文的阻塞点。"

    return {
        "useful": useful,
        "category": category,
        "reason": reason if useful else "",
        "suggested_inputs": suggested_inputs if useful else [],
        "recommended_action": recommended_action,
        "assisted_rerun_enabled": useful,
        "requires_cancel_current": requires_cancel_current,
        "can_cancel_current": requires_cancel_current,
        "status": normalized_status,
    }


_HISTORY_ISSUE_STATUSES = {"failed", "bug_found"}
_HISTORY_ACTIVE_STATUSES = {"pending", "queued", "running"}
_HISTORY_TERMINAL_STATUSES = {"succeeded", "failed", "bug_found", "cancelled"}


def _history_status(task: Task) -> str:
    status = getattr(task, "status", "")
    return status.value if hasattr(status, "value") else str(status)


def _history_created_at(task: Task, fallback: datetime) -> datetime:
    value = getattr(task, "created_at", None)
    return value if isinstance(value, datetime) else fallback


def _history_iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _history_percent(part: int, total: int) -> float:
    return round((part / total) * 100, 1) if total else 0


def _history_safe_target(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "Unknown target"
    if text.startswith(("http://", "https://")):
        return _triage_text(_redact_url_for_preview(text), 180)
    lowered = text[:40].lstrip().lower()
    if lowered.startswith(("{", "[", "openapi:", "swagger:")):
        return "Pasted OpenAPI document"
    return _triage_text(text, 180) or "Unknown target"


def _history_status_counts(tasks: list[Task]) -> dict[str, Any]:
    counts = {status.value: 0 for status in TaskStatus}
    for task in tasks:
        status = _history_status(task)
        counts[status] = counts.get(status, 0) + 1

    completed = counts.get("succeeded", 0) + counts.get("failed", 0) + counts.get("bug_found", 0)
    issues = counts.get("failed", 0) + counts.get("bug_found", 0)
    total = len(tasks)
    return {
        "total": total,
        "pending": counts.get("pending", 0),
        "queued": counts.get("queued", 0),
        "running": counts.get("running", 0),
        "succeeded": counts.get("succeeded", 0),
        "failed": counts.get("failed", 0),
        "bug_found": counts.get("bug_found", 0),
        "cancelled": counts.get("cancelled", 0),
        "active": counts.get("pending", 0) + counts.get("queued", 0) + counts.get("running", 0),
        "completed": completed,
        "pass_rate": _history_percent(counts.get("succeeded", 0), completed),
        "issue_rate": _history_percent(issues, completed),
        "bug_rate": _history_percent(counts.get("bug_found", 0), completed),
    }


def _history_issue_rate(tasks: list[Task]) -> float | None:
    completed = [task for task in tasks if _history_status(task) in _HISTORY_TERMINAL_STATUSES]
    if not completed:
        return None
    issues = sum(1 for task in completed if _history_status(task) in _HISTORY_ISSUE_STATUSES)
    return _history_percent(issues, len(completed))


def _history_trend_buckets(tasks: list[Task], now: datetime, days: int) -> list[dict[str, Any]]:
    bucket_days = min(days, 14)
    buckets: dict[str, dict[str, Any]] = {}
    for index in range(bucket_days - 1, -1, -1):
        day = now - timedelta(days=index)
        label = day.strftime("%m-%d")
        buckets[label] = {
            "date": label,
            "total": 0,
            "succeeded": 0,
            "failed": 0,
            "bug_found": 0,
            "cancelled": 0,
            "active": 0,
        }

    for task in tasks:
        label = _history_created_at(task, now).strftime("%m-%d")
        if label not in buckets:
            continue
        status = _history_status(task)
        bucket = buckets[label]
        bucket["total"] += 1
        if status in {"succeeded", "failed", "bug_found", "cancelled"}:
            bucket[status] += 1
        if status in _HISTORY_ACTIVE_STATUSES:
            bucket["active"] += 1
    return list(buckets.values())


def _history_quality_trend(tasks: list[Task], now: datetime, days: int) -> dict[str, Any]:
    terminal_tasks = [
        task
        for task in sorted(tasks, key=lambda item: _history_created_at(item, now))
        if _history_status(task) in _HISTORY_TERMINAL_STATUSES
    ]
    buckets = _history_trend_buckets(tasks, now, days)
    if len(terminal_tasks) < 4:
        return {
            "direction": "insufficient",
            "label": "样本不足",
            "rationale": "近期完成运行少于 4 次，继续积累后再判断质量趋势。",
            "recent_issue_rate": _history_issue_rate(terminal_tasks),
            "previous_issue_rate": None,
            "buckets": buckets,
        }

    midpoint = len(terminal_tasks) // 2
    previous = terminal_tasks[:midpoint]
    recent = terminal_tasks[midpoint:]
    previous_rate = _history_issue_rate(previous) or 0
    recent_rate = _history_issue_rate(recent) or 0
    delta = recent_rate - previous_rate

    if delta >= 10:
        direction = "regressing"
        label = "问题增多"
        rationale = f"近期问题率从 {previous_rate}% 升至 {recent_rate}%，建议优先复核反复失败项。"
    elif delta <= -10:
        direction = "improving"
        label = "质量改善"
        rationale = f"近期问题率从 {previous_rate}% 降至 {recent_rate}%，继续保留当前验证节奏。"
    else:
        direction = "stable"
        label = "趋势稳定"
        rationale = f"近期问题率 {recent_rate}%，与上一批运行差异不大。"

    return {
        "direction": direction,
        "label": label,
        "rationale": rationale,
        "recent_issue_rate": recent_rate,
        "previous_issue_rate": previous_rate,
        "buckets": buckets,
    }


def _history_theme_category(finding: dict[str, Any]) -> str:
    source = str(finding.get("source") or "").lower()
    if source == "api":
        return "api"
    if source.startswith("ui"):
        return "ui"
    return "report"


def _history_normalize_theme(value: Any) -> str:
    text = _triage_text(value, 220).lower()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\b\d{2,}\b", " # ", text)
    text = re.sub(r"[/_?=&.,:;()\\[\\]{}\"']+", " ", text)
    tokens = [
        token
        for token in text.split()
        if token
        and token not in {
            "the",
            "a",
            "an",
            "and",
            "or",
            "with",
            "without",
            "failed",
            "failure",
            "error",
        }
    ]
    return " ".join(tokens[:8]) or text[:80] or "unknown"


def _history_theme_action(category: str, severity: str, theme: str) -> str:
    if severity in {"CRITICAL", "HIGH"}:
        return f"优先修复反复出现的 {theme}，修复后重跑受影响范围。"
    if category == "api":
        return "复核接口断言、状态码和业务错误码，补充稳定的回归用例。"
    if category == "ui":
        return "复核页面状态、权限和选择器稳定性，保留截图证据后重跑。"
    return "整理缺陷证据和复现步骤，归并同类问题后逐项关闭。"


def _history_add_last_seen(current: datetime | None, candidate: datetime) -> datetime:
    if current is None or candidate > current:
        return candidate
    return current


def _build_history_recommendations(
    *,
    counts: dict[str, Any],
    trend: dict[str, Any],
    themes: list[dict[str, Any]],
    affected_surfaces: list[dict[str, Any]],
    evidence: dict[str, Any],
) -> list[str]:
    actions: list[str] = []
    if counts["total"] == 0:
        actions.append("暂无历史运行；先从 /run 发起一次可复核的测试任务。")
    if counts["active"]:
        actions.append(f"当前有 {counts['active']} 次运行未完成；等待完成后再确认质量趋势。")
    if trend.get("direction") == "regressing":
        actions.append("近期问题率上升；先冻结受影响目标的发布判断，并重跑失败路径。")
    if themes:
        actions.append(f"优先处理反复出现的「{themes[0]['theme']}」，避免继续消耗回归时间。")
    elif counts["failed"] or counts["bug_found"]:
        actions.append("已有失败或缺陷运行；把失败项归并成可复用回归用例。")
    if affected_surfaces:
        actions.append(f"重点复核 {affected_surfaces[0]['name']}，它是近期最常受影响的测试面。")
    if counts["completed"] and evidence["runs_with_evidence"] < counts["completed"]:
        actions.append("部分运行缺少可评审证据；优先保留 API 结果、截图和复现脚本。")
    if counts["completed"] and counts["issue_rate"] == 0:
        actions.append("近期已完成运行未发现阻断问题；保留证据作为发布评审记忆。")
    return list(dict.fromkeys(actions))[:6]


def _build_run_history_insights(
    tasks: list[Task],
    *,
    now: datetime,
    days: int,
    limit: int,
    window_run_count: int,
) -> dict[str, Any]:
    ordered_tasks = sorted(tasks, key=lambda item: _history_created_at(item, now), reverse=True)
    counts = _history_status_counts(ordered_tasks)
    trend = _history_quality_trend(ordered_tasks, now, days)

    target_stats: dict[str, dict[str, Any]] = {}
    surface_stats: dict[tuple[str, str], dict[str, Any]] = {}
    theme_stats: dict[str, dict[str, Any]] = {}
    evidence = {
        "runs_with_evidence": 0,
        "runs_with_api_evidence": 0,
        "runs_with_screenshots": 0,
        "runs_with_tool_calls": 0,
        "runs_with_reproduction": 0,
        "runs_with_scripts": 0,
    }

    for task in ordered_tasks:
        status = _history_status(task)
        created_at = _history_created_at(task, now)
        parsed = redact_sensitive_data(_parse_execution_log_dict(getattr(task, "execution_log", None)))
        triage = _build_run_triage_summary(status, parsed)
        target = _history_safe_target(getattr(task, "target_url", None))
        issue_run = status in _HISTORY_ISSUE_STATUSES or _triage_int(triage.get("blocking_count")) > 0

        target_entry = target_stats.setdefault(
            target,
            {
                "target": target,
                "run_count": 0,
                "issue_run_count": 0,
                "failed_count": 0,
                "bug_count": 0,
                "last_seen_dt": None,
            },
        )
        target_entry["run_count"] += 1
        target_entry["last_seen_dt"] = _history_add_last_seen(target_entry["last_seen_dt"], created_at)
        if issue_run:
            target_entry["issue_run_count"] += 1
        if status == "failed":
            target_entry["failed_count"] += 1
        if status == "bug_found":
            target_entry["bug_count"] += 1

        evidence_info = _triage_dict(triage.get("evidence"))
        reproduction = _triage_dict(triage.get("reproduction"))
        if _triage_int(evidence_info.get("count")):
            evidence["runs_with_evidence"] += 1
        if _triage_int(evidence_info.get("api_result_count")):
            evidence["runs_with_api_evidence"] += 1
        if _triage_int(evidence_info.get("screenshot_count")):
            evidence["runs_with_screenshots"] += 1
        if _triage_int(evidence_info.get("tool_call_count")):
            evidence["runs_with_tool_calls"] += 1
        if reproduction.get("available"):
            evidence["runs_with_reproduction"] += 1
        if reproduction.get("script_available"):
            evidence["runs_with_scripts"] += 1

        if not issue_run:
            continue

        surfaces = [
            item
            for item in _triage_list(triage.get("affected_surfaces"))
            if isinstance(item, dict) and item.get("name")
        ]
        if not surfaces:
            surfaces = [{"type": "target", "name": target, "detail": "运行失败或发现缺陷。"}]

        for surface in surfaces:
            surface_type = _triage_text(surface.get("type") or "target", 60)
            surface_name = _triage_text(surface.get("name"), 160)
            if not surface_name:
                continue
            key = (surface_type, surface_name)
            entry = surface_stats.setdefault(
                key,
                {
                    "type": surface_type,
                    "name": surface_name,
                    "issue_count": 0,
                    "last_seen_dt": None,
                    "detail": None,
                },
            )
            entry["issue_count"] += 1
            entry["last_seen_dt"] = _history_add_last_seen(entry["last_seen_dt"], created_at)
            if not entry.get("detail") and surface.get("detail"):
                entry["detail"] = _triage_text(surface.get("detail"), 180)

        for finding in _triage_list(triage.get("blocking_findings")):
            if not isinstance(finding, dict):
                continue
            category = _history_theme_category(finding)
            title = _triage_text(finding.get("title") or finding.get("description"), 180)
            if not title:
                continue
            theme_key = f"{category}:{_history_normalize_theme(title)}"
            severity = _triage_severity(finding.get("severity"))
            entry = theme_stats.setdefault(
                theme_key,
                {
                    "theme": title,
                    "category": category,
                    "count": 0,
                    "severity": severity,
                    "severity_rank": _triage_severity_rank(severity),
                    "surfaces": set(),
                    "examples": [],
                    "last_seen_dt": None,
                },
            )
            entry["count"] += 1
            entry["last_seen_dt"] = _history_add_last_seen(entry["last_seen_dt"], created_at)
            if _triage_severity_rank(severity) > entry["severity_rank"]:
                entry["severity"] = severity
                entry["severity_rank"] = _triage_severity_rank(severity)
            surface_name = _triage_text(finding.get("surface"), 160)
            if surface_name:
                entry["surfaces"].add(surface_name)
            if title not in entry["examples"] and len(entry["examples"]) < 3:
                entry["examples"].append(title)

    affected_targets = [
        {
            "target": item["target"],
            "run_count": item["run_count"],
            "issue_run_count": item["issue_run_count"],
            "failed_count": item["failed_count"],
            "bug_count": item["bug_count"],
            "last_seen": _history_iso(item["last_seen_dt"]),
        }
        for item in target_stats.values()
        if item["issue_run_count"] > 0
    ]
    affected_targets.sort(
        key=lambda item: (
            item["issue_run_count"],
            item["bug_count"],
            item["failed_count"],
            item["last_seen"] or "",
        ),
        reverse=True,
    )

    affected_surfaces = [
        {
            "type": item["type"],
            "name": item["name"],
            "issue_count": item["issue_count"],
            "last_seen": _history_iso(item["last_seen_dt"]),
            "detail": item.get("detail"),
        }
        for item in surface_stats.values()
    ]
    affected_surfaces.sort(
        key=lambda item: (item["issue_count"], item["last_seen"] or ""),
        reverse=True,
    )

    recurring_themes = [
        {
            "theme": item["theme"],
            "category": item["category"],
            "count": item["count"],
            "severity": item["severity"],
            "surfaces": sorted(item["surfaces"])[:5],
            "examples": item["examples"][:3],
            "last_seen": _history_iso(item["last_seen_dt"]),
            "recommended_action": _history_theme_action(
                item["category"],
                item["severity"],
                item["theme"],
            ),
        }
        for item in theme_stats.values()
        if item["count"] > 1
    ]
    recurring_themes.sort(
        key=lambda item: (
            item["count"],
            _triage_severity_rank(item["severity"]),
            item["last_seen"] or "",
        ),
        reverse=True,
    )

    evidence["evidence_rate"] = _history_percent(evidence["runs_with_evidence"], len(ordered_tasks))
    evidence["reproduction_rate"] = _history_percent(
        evidence["runs_with_reproduction"],
        counts["failed"] + counts["bug_found"],
    )
    recommended_actions = _build_history_recommendations(
        counts=counts,
        trend=trend,
        themes=recurring_themes,
        affected_surfaces=affected_surfaces,
        evidence=evidence,
    )

    return {
        "generated_at": now.isoformat(),
        "window_days": days,
        "sample_limit": limit,
        "window_run_count": window_run_count,
        "analyzed_runs": len(ordered_tasks),
        "status_counts": counts,
        "quality_trend": trend,
        "affected_targets": affected_targets[:6],
        "affected_surfaces": affected_surfaces[:8],
        "recurring_themes": recurring_themes[:6],
        "evidence_reproduction": evidence,
        "recommended_next_actions": recommended_actions,
    }


def _rehydratable_headers(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}

    headers: dict[str, str] = {}
    for key, header_value in value.items():
        key_text = str(key).strip()
        if not key_text or is_sensitive_header(key_text):
            continue
        if header_value in (None, "", REDACTED_VALUE):
            continue
        if isinstance(header_value, str):
            value_text = header_value.strip()
        elif isinstance(header_value, (int, float, bool)):
            value_text = str(header_value)
        else:
            continue
        if (
            not value_text
            or REDACTED_VALUE in value_text
            or redact_sensitive_text(value_text) != value_text
        ):
            continue
        headers[key_text] = value_text
    return headers


def _rerun_context_from_task(task) -> dict[str, Any]:
    log_data = _parse_execution_log_dict(getattr(task, "execution_log", None))
    context: dict[str, Any] = {}

    for key in (
        "source_input",
        "api_cases",
        "ui_cases",
        "setup_instructions",
        "login_instructions",
        "base_url_override",
        "api_execution_policy",
        "api_path_prefix_rewrite",
        "ui_seed_url",
        "input_type",
    ):
        value = log_data.get(key)
        if value not in (None, "", [], {}):
            context[key] = value

    auth_headers = _rehydratable_headers(log_data.get("auth_headers"))
    if auth_headers:
        context["auth_headers"] = auth_headers

    custom_headers = _rehydratable_headers(log_data.get("custom_headers"))
    if custom_headers:
        context["custom_headers"] = custom_headers

    setup_value = context.get("setup_instructions") or context.get("login_instructions")
    if setup_value:
        context["setup_instructions"] = setup_value
        context["login_instructions"] = setup_value

    if not context.get("source_input") and context.get("ui_seed_url"):
        context["source_input"] = context["ui_seed_url"]
    context.setdefault("source_input", getattr(task, "target_url", "") or "")
    return context


def _append_intervention_instructions(existing: Any, supplemental: str) -> str:
    existing_text = str(existing or "").strip()
    supplemental_text = supplemental.strip()
    intervention_block = (
        "人工干预补充上下文：\n"
        f"{supplemental_text}\n"
        "请在本次重跑中优先使用这些补充说明完成登录、环境准备或受阻步骤。"
    )
    if not existing_text:
        return intervention_block
    return f"{existing_text}\n\n{intervention_block}"


def _intervention_target_url(task: Task, rerun_context: dict[str, Any]) -> str:
    if rerun_context.get("input_type") == "url" and rerun_context.get("ui_seed_url"):
        return str(rerun_context["ui_seed_url"])
    return str(getattr(task, "target_url", "") or rerun_context.get("source_input") or "")


async def _seed_intervention_execution_log(
    db: DbSession,
    task: Task,
    *,
    source_run_id: str,
    rerun_context: dict[str, Any],
) -> None:
    initial_log: dict[str, Any] = {
        "intervention_context": {
            "source_run_id": source_run_id,
            "applied": True,
            "detail": "Human supplemental context was applied to this assisted rerun. Secret-bearing values are redacted in persisted logs.",
            "created_at": datetime.utcnow().isoformat(),
        }
    }
    for key in (
        "source_input",
        "input_type",
        "ui_seed_url",
        "base_url_override",
        "api_execution_policy",
        "api_path_prefix_rewrite",
        "setup_instructions",
        "login_instructions",
    ):
        value = rerun_context.get(key)
        if value not in (None, "", [], {}):
            initial_log[key] = value

    task.execution_log = json.dumps(redact_sensitive_data(initial_log), ensure_ascii=False, default=str)
    await db.commit()
    await db.refresh(task)


def _revoke_worker_task(run_id: str) -> None:
    try:
        from app.worker.celery_app import celery_app

        celery_app.control.revoke(run_id, terminate=True)
    except Exception as e:
        logger.warning("Celery revoke failed for run %s: %s", run_id, e)


async def _cancel_active_task(db: DbSession, task: Task, detail: str) -> Task:
    current_status = _status_value(task.status)
    if current_status not in _INTERVENTION_ACTIVE_STATUSES:
        raise HTTPException(status_code=400, detail="Run is not in a cancellable state")
    _revoke_worker_task(task.id)
    return await mark_task_cancelled(db, task, detail)


@router.post("", response_model=TaskRead)
async def create_run(payload: RunCreate, db: DbSession, _: CurrentUser):
    """Create a new test run from source input (URL, Swagger URL, or Swagger text)."""
    from app.agent.nodes.source_loader import classify_input

    source = payload.source.strip()
    if not source:
        raise HTTPException(status_code=400, detail="source is required")

    try:
        db_test_type = normalize_test_type(payload.test_type, default=TestType.AUTO)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    agent_test_type = normalize_agent_test_type(db_test_type, default="auto")

    input_type = classify_input(source)

    target_url = _resolve_run_target_url(source, input_type, payload.base_url)

    # Smart objective: use provided, or try to extract from Swagger title
    objective = payload.objective or f"Auto test from {input_type}"
    if not payload.objective and input_type in ("swagger_url", "swagger_json", "swagger_yaml"):
        try:
            if input_type == "swagger_url":
                async with httpx.AsyncClient(timeout=10.0) as c:
                    resp = await c.get(source)
                    text = resp.text
            else:
                text = source
            text = text.strip()
            if text.startswith("{"):
                import json as _json
                data = _json.loads(text)
                title = (data.get("info") or {}).get("title", "")
                if title:
                    objective = f"测试 {title} API"
            elif text.startswith("openapi:") or text.startswith("swagger:"):
                # Basic YAML title extraction
                for line in text.split("\n"):
                    line = line.strip()
                    if line.startswith("title:"):
                        title = line.split(":", 1)[1].strip().strip("'\"")
                        if title:
                            objective = f"测试 {title} API"
                            break
        except Exception:
            pass

    from app.agent.nodes.api_runner import _normalize_api_execution_policy
    api_execution_policy = _normalize_api_execution_policy(payload.api_execution_policy)
    api_profile = await _best_effort_api_profile(source, input_type, api_execution_policy)
    if not payload.base_url and api_profile.get("target_url"):
        target_url = str(api_profile["target_url"])
    extra_headers, auth_resolution = await _prepare_run_auth(
        payload,
        source=source,
        input_type=input_type,
        target_url=target_url,
    )
    runtime_auth_config = coerce_auth_config(payload.auth_config)
    if not runtime_auth_config.get("enabled"):
        runtime_auth_config = None
    auth_required_count = api_profile.get("auth_required_count")
    has_auth = bool((payload.token or "").strip() or has_auth_like_header(extra_headers))
    if auth_required_count and not has_auth:
        detail = (
            f"检测到 {auth_required_count} 个接口需要鉴权。请提供 Token/Header，"
            "或选择自动获取 Token 并填写可通过的基础登录凭据后再执行。"
        )
        if payload.auth_config and payload.auth_config.enabled and auth_resolution.detail:
            detail = f"{detail} 自动获取 Token 失败：{auth_resolution.detail}"
            if auth_resolution.next_action:
                detail = f"{detail} {auth_resolution.next_action}"
        raise HTTPException(status_code=400, detail=detail)

    task = await task_service.create(
        db,
        objective=objective,
        target_url=target_url,
        test_type=db_test_type,
        status=TaskStatus.QUEUED,
    )

    setup_instructions = _resolve_setup_instructions(payload)

    try:
        run_agent_task.delay(
            task.id,
            objective,
            target_url,
            test_type=agent_test_type,
            source_input=source,
            ui_seed_url=source if input_type == "url" else None,
            input_type=input_type,
            auth_headers=extra_headers or None,
            auth_config=runtime_auth_config,
            base_url_override=payload.base_url,
            api_execution_policy=api_execution_policy,
            setup_instructions=setup_instructions,
            login_instructions=setup_instructions,
        )
    except Exception as e:
        logger.warning("Celery dispatch failed: %s, running synchronously", e)
        final_state = await run_graph_with_progress(
            {
                "task_id": task.id,
                "objective": objective,
                "target_url": target_url,
                "test_type": agent_test_type,
                "source_input": source,
                "ui_seed_url": source if input_type == "url" else None,
                "input_type": input_type,
                "auth_headers": extra_headers or None,
                "auth_config": runtime_auth_config,
                "base_url_override": payload.base_url,
                "api_execution_policy": api_execution_policy,
                "setup_instructions": setup_instructions,
                "login_instructions": setup_instructions,
                "retry_count": 0,
                "messages": [],
                "workflow_steps": [],
                "db_session": db,
            }
        )
        await _persist_state(db, task, final_state)

    return task


@router.post("/preflight", response_model=RunPreflightResponse)
async def preflight_run(payload: RunPreflightRequest, db: DbSession, _: CurrentUser):
    """Inspect run input and product readiness before creating a test run."""
    from app.agent.nodes.api_runner import _normalize_api_execution_policy
    from app.agent.nodes.source_loader import classify_input

    source = payload.source.strip()
    if not source:
        raise HTTPException(status_code=400, detail="source is required")

    try:
        db_test_type = normalize_test_type(payload.test_type, default=TestType.AUTO)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    agent_test_type = normalize_agent_test_type(db_test_type, default="auto")

    input_type = classify_input(source)
    api_execution_policy = _normalize_api_execution_policy(payload.api_execution_policy)
    target_url = _resolve_run_target_url(source, input_type, payload.base_url)
    provider_count = await _count_rows(db, LLMProvider)
    planner_count = await _count_default_planners(db)
    environment_count = await _count_rows(db, Environment)
    api_profile = await _best_effort_api_profile(source, input_type, api_execution_policy)
    endpoint_count = api_profile["endpoint_count"]
    auth_required_count = api_profile["auth_required_count"]
    estimated_executable_count = api_profile["estimated_executable_count"]
    estimated_skipped_count = api_profile["estimated_skipped_count"]
    if not payload.base_url and api_profile.get("target_url"):
        target_url = str(api_profile["target_url"])
    reachability = await _best_effort_reachability(source)
    worker_status, worker_detail, worker_action = await _best_effort_worker_readiness()
    browser_tool_found = shutil.which("playwright-cli") is not None or shutil.which("npx") is not None
    prepared_headers, auth_resolution = await _prepare_run_auth(
        payload,
        source=source,
        input_type=input_type,
        target_url=target_url,
    )
    supplied_auth = bool((payload.token or "").strip() or has_auth_like_header(prepared_headers))
    auth_attempted = bool(payload.auth_config and payload.auth_config.enabled)

    checks = [
        RunPreflightCheck(
            key="source",
            label="测试目标",
            status="ready",
            detail=f"已识别为 {input_type}",
        ),
        RunPreflightCheck(
            key="provider",
            label="模型配置",
            status="ready" if provider_count else "missing",
            detail="已配置可用模型" if provider_count else "尚未配置 LLM Provider",
            action=None if provider_count else "前往系统设置配置模型",
        ),
        RunPreflightCheck(
            key="planner",
            label="规划模型",
            status="ready" if planner_count else "warning",
            detail="已有默认 Planner 模型" if planner_count else "未设置默认 Planner，系统将按现有回退逻辑尝试运行",
            action=None if planner_count else "在模型管理中设置默认 Planner",
        ),
        RunPreflightCheck(
            key="worker",
            label="任务 Worker",
            status=worker_status,
            detail=worker_detail,
            action=worker_action,
        ),
        RunPreflightCheck(
            key="runner",
            label="浏览器执行器",
            status="ready" if browser_tool_found else "warning",
            detail="检测到本地浏览器执行入口" if browser_tool_found else "未检测到 playwright-cli 或 npx，UI 测试可能失败",
            action=None if browser_tool_found else "确认前端/Worker 镜像已安装浏览器工具",
        ),
        RunPreflightCheck(
            key="reachability",
            label="目标可达性",
            status=reachability,
            detail="目标可访问" if reachability == "ready" else "未执行网络检查" if reachability == "skipped" else "暂时无法确认目标可达",
        ),
        RunPreflightCheck(
            key="environment",
            label="环境资产",
            status="ready" if environment_count else "warning",
            detail=f"已配置 {environment_count} 个环境" if environment_count else "尚未沉淀环境配置，本次将使用输入源直接运行",
        ),
        RunPreflightCheck(
            key="auth",
            label="鉴权准备",
            status="ready" if not auth_required_count or supplied_auth else "missing",
            detail=(
                "未检测到鉴权要求"
                if not auth_required_count
                else "自动获取 Token 已通过，运行时会注入鉴权头"
                if auth_resolution.ok
                else "已提供 Token/Header"
                if supplied_auth
                else (
                    f"检测到 {auth_required_count} 个接口需要鉴权，且自动获取 Token 未通过：{auth_resolution.detail}"
                    if auth_attempted
                    else f"检测到 {auth_required_count} 个接口需要鉴权，必须提供 Token/Header 或选择自动获取 Token"
                )
            ),
            action=(
                None
                if not auth_required_count or supplied_auth
                else auth_resolution.next_action or "选择自动获取 Token，或手动提供 Token/Header"
            ),
        ),
        RunPreflightCheck(
            key="api_policy",
            label="API 执行策略",
            status="ready",
            detail=(
                "安全只读：默认跳过 POST/PUT/PATCH/DELETE"
                if api_execution_policy != "write_allowed"
                else "已允许写入/变更请求，需确认目标为测试环境"
            ),
        ),
    ]

    warnings: list[str] = []
    if input_type in ("swagger_json", "swagger_yaml") and not payload.base_url:
        warnings.append("原文 Swagger 未提供 Base URL 时，系统只能依赖文档 servers 字段推断请求地址。")
    if agent_test_type in ("ui", "auto") and not payload.setup_instructions.strip():
        warnings.append("如果目标需要登录、验证码或其他前置步骤，请在前置说明里提供测试账号和安全边界。")
    if input_type == "url" and agent_test_type == "api":
        warnings.append("当前输入看起来是网页 URL，但测试模式选择了 API；建议确认是否应使用 Swagger/OpenAPI。")
    if auth_required_count and not supplied_auth:
        warnings.append(f"检测到 {auth_required_count} 个接口声明需要鉴权；未提供可用鉴权时不会允许创建 API 任务。")
    if auth_attempted and not auth_resolution.ok:
        warnings.append(f"自动获取 Token 预检失败：{auth_resolution.detail}")
    if estimated_skipped_count:
        warnings.append(f"当前 API 策略预计会跳过 {estimated_skipped_count} 个写入/变更接口，避免误改真实数据。")
    if api_profile.get("api_path_prefix_rewrite"):
        rewrite = api_profile["api_path_prefix_rewrite"]
        warnings.append(f"检测到代理路径改写：请求执行时会将 {rewrite.get('from')} 改为 {rewrite.get('to')}。")

    expected_flow = _expected_flow_for(input_type, agent_test_type, bool(payload.base_url))
    readiness = _preflight_readiness(checks)
    mission_preview = _build_mission_preview(
        payload,
        source=source,
        input_type=input_type,
        test_type=agent_test_type,
        target_url=target_url,
        expected_flow=expected_flow,
        readiness=readiness,
        checks=checks,
        warnings=warnings,
        endpoint_count=endpoint_count,
        auth_required_count=auth_required_count,
        estimated_executable_count=estimated_executable_count,
        estimated_skipped_count=estimated_skipped_count,
        api_execution_policy=api_execution_policy,
        supplied_auth=supplied_auth,
        auth_resolution=auth_resolution,
    )
    target_memory = await _build_preflight_target_memory(
        db,
        source=source,
        input_type=input_type,
        target_url=target_url,
    )

    return RunPreflightResponse(
        input_type=input_type,
        test_type=agent_test_type,
        target_url=target_url,
        expected_flow=expected_flow,
        readiness=readiness,
        checks=checks,
        mission_preview=mission_preview,
        target_memory=target_memory,
        warnings=warnings,
        endpoint_count=endpoint_count,
        auth_required_count=auth_required_count,
        estimated_executable_count=estimated_executable_count,
        estimated_skipped_count=estimated_skipped_count,
        api_execution_policy=api_execution_policy,
        api_path_prefix_rewrite=api_profile.get("api_path_prefix_rewrite"),
        auth_resolved=auth_resolution.ok,
        auth_strategy=auth_resolution.strategy,
        auth_header_name=auth_resolution.header_name,
        auth_error=None if auth_resolution.ok or not auth_attempted else auth_resolution.detail,
        auth_missing_inputs=[] if auth_resolution.ok or not auth_attempted else auth_resolution.missing_inputs,
        auth_next_action=None if auth_resolution.ok or not auth_attempted else auth_resolution.next_action,
        auth_required_fields=[] if auth_resolution.ok or not auth_attempted else auth_resolution.required_fields,
    )


@router.get("", response_model=list[TaskListItemRead])
async def list_runs(
    db: DbSession, _: CurrentUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: str | None = Query(default=None),
    test_type: str | None = Query(default=None),
):
    """List all test runs with optional filters."""
    try:
        normalized_status = normalize_task_status(status) if status else None
        normalized_test_type = normalize_test_type(test_type) if test_type else None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    filters: list[Any] = []
    if normalized_status is not None:
        filters.append(Task.status == normalized_status)
    if normalized_test_type is not None:
        filters.append(Task.test_type == normalized_test_type)

    count_stmt = select(func.count()).select_from(Task)
    if filters:
        count_stmt = count_stmt.where(*filters)
    total = int((await db.execute(count_stmt)).scalar_one())

    offset = (page - 1) * page_size
    stmt = (
        select(
            Task.id,
            Task.target_url,
            Task.objective,
            Task.status,
            Task.test_type,
            Task.created_at,
            Task.updated_at,
        )
    )
    if filters:
        stmt = stmt.where(*filters)
    stmt = stmt.order_by(Task.created_at.desc()).offset(offset).limit(page_size)

    result = await db.execute(stmt)
    items = [
        TaskListItemRead(
            id=row.id,
            target_url=row.target_url,
            objective=row.objective,
            status=_status_value(row.status),
            test_type=_status_value(row.test_type),
            created_at=row.created_at,
            updated_at=row.updated_at,
        ).model_dump(mode="json")
        for row in result
    ]
    return JSONResponse(
        content=items,
        headers={"X-Total-Count": str(total)},
    )


@router.get("/insights", response_model=RunHistoryInsightsResponse)
async def get_run_history_insights(
    db: DbSession,
    _: CurrentUser,
    days: int = Query(default=30, ge=1, le=90),
    limit: int = Query(default=100, ge=1, le=200),
):
    """Summarize recent run quality memory from task rows and redacted execution logs."""
    now = datetime.utcnow()
    cutoff = now - timedelta(days=days)
    count_result = await db.execute(
        select(func.count()).select_from(Task).where(Task.created_at >= cutoff)
    )
    window_run_count = int(count_result.scalar_one())
    result = await db.execute(
        select(Task)
        .where(Task.created_at >= cutoff)
        .order_by(Task.created_at.desc())
        .limit(limit)
    )
    tasks = list(result.scalars())
    return _build_run_history_insights(
        tasks,
        now=now,
        days=days,
        limit=limit,
        window_run_count=window_run_count,
    )


@router.get("/{run_id}")
async def get_run_detail(run_id: str, db: DbSession, _: CurrentUser):
    """Get full run detail including plan, cases, API/UI results, screenshots, and triage summary."""
    task = await task_service.get(db, run_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Run not found")
    detail = parse_task_detail(task)

    # Enrich with additional fields from execution_log
    log_str = getattr(task, "execution_log", None) or ""
    try:
        parsed = json.loads(log_str) if log_str else {}
    except Exception:
        parsed = {}
    parsed = redact_sensitive_data(parsed)
    if log_str:
        detail["execution_log"] = redact_json_text(log_str)

    detail["api_plan"] = parsed.get("api_plan")
    detail["ui_plan"] = parsed.get("ui_plan")
    detail["api_cases"] = parsed.get("api_cases")
    detail["ui_cases"] = parsed.get("ui_cases")
    detail["api_execution_result"] = parsed.get("api_execution_result")
    detail["ui_execution_result"] = parsed.get("ui_execution_result")
    detail["final_report"] = parsed.get("final_report")
    detail["artifacts"] = parsed.get("artifacts")
    detail["tool_registry"] = parsed.get("tool_registry")
    detail["skill_plan"] = parsed.get("skill_plan")
    detail["tool_calls"] = parsed.get("tool_calls")
    detail["tool_summary"] = parsed.get("tool_summary")
    detail["input_type"] = parsed.get("input_type")
    detail["source_input"] = parsed.get("source_input")
    detail["current_step"] = parsed.get("current_step")
    detail["progress_events"] = parsed.get("progress_events", [])
    detail["cancelled"] = parsed.get("cancelled", False)
    detail["cancelled_at"] = parsed.get("cancelled_at")
    detail["last_error"] = parsed.get("last_error")
    detail["setup_instructions"] = parsed.get("setup_instructions") or parsed.get("login_instructions")
    detail["login_instructions"] = parsed.get("login_instructions")
    detail["setup_result"] = parsed.get("setup_result")
    detail["ui_login_snapshot"] = parsed.get("ui_login_snapshot")
    detail["login_playwright_commands"] = parsed.get("login_playwright_commands")
    detail["ui_reproducible_script"] = parsed.get("ui_reproducible_script")
    detail["scene_hints"] = parsed.get("scene_hints")
    detail["auth_chain"] = parsed.get("auth_chain")
    detail["api_execution_policy"] = parsed.get("api_execution_policy")
    detail["api_path_prefix_rewrite"] = parsed.get("api_path_prefix_rewrite")
    detail["triage_summary"] = _build_run_triage_summary(str(detail.get("status") or ""), parsed)
    detail["intervention_summary"] = _build_run_intervention_summary(
        str(detail.get("status") or ""),
        parsed,
        detail["triage_summary"],
    )

    return detail


@router.get("/{run_id}/triage-export")
async def export_run_triage(
    run_id: str,
    db: DbSession,
    _: CurrentUser,
    export_format: str = Query(default="markdown", alias="format"),
):
    """Export a safe triage handoff artifact as Markdown or JSON."""
    normalized_format = export_format.lower().strip()
    if normalized_format not in _TRIAGE_EXPORT_FORMATS:
        raise HTTPException(status_code=400, detail="format must be markdown or json")

    task = await task_service.get(db, run_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Run not found")

    parsed = redact_sensitive_data(_parse_execution_log_dict(task.execution_log))
    status = _status_value(task.status)
    triage_summary = _build_run_triage_summary(status, parsed)
    export_model = await _build_run_triage_export(db, task, parsed, triage_summary)
    payload = export_model.model_dump(mode="json")
    headers = {
        "Content-Disposition": _triage_export_content_disposition(run_id, normalized_format),
    }

    if normalized_format == "json":
        return JSONResponse(content=payload, headers=headers)

    return Response(
        content=_render_run_triage_export_markdown(payload),
        media_type="text/markdown",
        headers=headers,
    )


@router.post("/{run_id}/case-assets", response_model=RunCaseAssetsResponse)
async def save_run_case_assets(
    run_id: str,
    payload: RunCaseAssetsCreate,
    db: DbSession,
    _: CurrentUser,
):
    """Persist accepted run-generated cases as reusable TestCase records and a suite."""
    task = await task_service.get(db, run_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if not payload.cases:
        raise HTTPException(status_code=400, detail="No accepted cases selected")

    parsed = _parse_execution_log_dict(task.execution_log)
    selected_keys: set[tuple[str, int]] = set()
    saved_cases: list[TestCase] = []
    saved_metadata: list[dict[str, Any]] = []

    for selection in payload.cases:
        key = (selection.source, selection.index)
        if key in selected_keys:
            raise HTTPException(
                status_code=400,
                detail=f"Duplicate case selection: {selection.source}[{selection.index}]",
            )
        selected_keys.add(key)

        original = _case_asset_source_case(parsed, selection.source, selection.index)
        if original is None:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid case selection: {selection.source}[{selection.index}]",
            )

        normalized = _normalize_case_asset_for_save(
            _case_asset_merge_case(original, selection.case),
            run_id=run_id,
            source=selection.source,
            source_index=selection.index,
        )
        case_type = normalized.pop("case_type")
        test_case = TestCase(**normalized)
        db.add(test_case)
        saved_cases.append(test_case)
        saved_metadata.append(
            {
                "source": selection.source,
                "source_index": selection.index,
                "case_type": case_type,
            }
        )

    await db.flush()

    suite_name = _case_asset_text(
        payload.suite_name,
        default=_default_case_asset_suite_name(task),
        limit=255,
    )
    suite = TestSuite(
        name=suite_name,
        test_case_ids=[test_case.id for test_case in saved_cases],
        task_id=run_id,
    )
    db.add(suite)
    await db.commit()
    await db.refresh(suite)
    for test_case in saved_cases:
        await db.refresh(test_case)

    return RunCaseAssetsResponse(
        suite_id=suite.id,
        suite_name=suite.name,
        case_ids=list(suite.test_case_ids or []),
        total=len(saved_cases),
        cases=[
            RunCaseAssetSavedCase(
                id=test_case.id,
                title=test_case.title,
                category=test_case.category,
                priority=test_case.priority,
                source=metadata["source"],
                source_index=metadata["source_index"],
                case_type=metadata["case_type"],
            )
            for test_case, metadata in zip(saved_cases, saved_metadata, strict=False)
        ],
    )


async def _verify_token_or_user(db, token: str | None = None):
    """Verify auth via query param token or fall back to CurrentUser dependency."""
    if token:
        from app.core.security import decode_access_token
        from app.models.user import User
        from sqlalchemy import select
        try:
            payload = decode_access_token(token)
            username = payload.get("sub")
            if not username:
                raise HTTPException(status_code=401, detail="Invalid token")
            result = await db.execute(select(User).where(User.username == username, User.is_active.is_(True)))
            user = result.scalar_one_or_none()
            if user is None:
                raise HTTPException(status_code=401, detail="User not found")
            return user
        except ValueError:
            raise HTTPException(status_code=401, detail="Invalid token")
    raise HTTPException(status_code=401, detail="Token required")


@router.get("/{run_id}/screenshots")
async def list_run_screenshots(run_id: str, db: DbSession, _: CurrentUser):
    """List available screenshot file names for a run."""
    task = await task_service.get(db, run_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Run not found")
    screenshot_dir = Path(settings.sandbox_dir) / "screenshots" / run_id
    if not screenshot_dir.exists():
        return {"screenshots": []}
    files = sorted(f.name for f in screenshot_dir.iterdir() if f.suffix == ".png")
    return {"screenshots": files}


@router.get("/{run_id}/screenshots/{filename}")
async def get_run_screenshot(
    run_id: str, filename: str, db: DbSession,
    token: str | None = Query(default=None),
):
    """Serve a screenshot file for a run."""
    await _verify_token_or_user(db, token)
    task = await task_service.get(db, run_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Run not found")
    # Sanitize filename to prevent path traversal
    safe_name = os.path.basename(filename)
    if not safe_name or not safe_name.endswith(".png"):
        raise HTTPException(status_code=400, detail="Invalid filename")
    file_path = Path(settings.sandbox_dir) / "screenshots" / run_id / safe_name
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return FileResponse(str(file_path), media_type="image/png")


@router.get("/{run_id}/stream")
async def stream_run(run_id: str, db: DbSession, token: str | None = Query(default=None)):
    """SSE stream for real-time run progress updates."""
    if token:
        from app.core.security import decode_access_token
        from app.models.user import User
        from sqlalchemy import select
        try:
            payload = decode_access_token(token)
            username = payload.get("sub")
            if not username:
                raise HTTPException(status_code=401, detail="Invalid token")
            result = await db.execute(select(User).where(User.username == username, User.is_active.is_(True)))
            user = result.scalar_one_or_none()
            if user is None:
                raise HTTPException(status_code=401, detail="User not found")
        except ValueError:
            raise HTTPException(status_code=401, detail="Invalid token")
    else:
        raise HTTPException(status_code=401, detail="Token required for SSE stream")

    async def event_stream():
        last_status = None
        last_log = ""
        while True:
            async with AsyncSessionLocal() as stream_db:
                task = await task_service.get(stream_db, run_id)
                if task is None:
                    yield f"data: {json.dumps({'error': 'Run not found'})}\n\n"
                    break
                current_status = task.status if isinstance(task.status, str) else task.status.value
                current_log = task.execution_log or ""

            if current_status != last_status:
                yield f"data: {json.dumps({'run_id': run_id, 'type': 'status', 'status': current_status})}\n\n"
                last_status = current_status

            if current_log != last_log:
                try:
                    log_data = json.loads(current_log)
                    log_data = redact_sensitive_data(log_data)
                    triage_summary = _build_run_triage_summary(current_status, log_data)
                    log_data["triage_summary"] = triage_summary
                    log_data["intervention_summary"] = _build_run_intervention_summary(
                        current_status,
                        log_data,
                        triage_summary,
                    )
                    yield f"data: {json.dumps({'run_id': run_id, 'type': 'snapshot', 'snapshot': log_data})}\n\n"
                    steps = log_data.get("workflow_steps") or []
                    if steps:
                        yield f"data: {json.dumps({'run_id': run_id, 'type': 'workflow', 'steps': steps})}\n\n"
                except Exception:
                    pass
                safe_log = redact_json_text(current_log) or current_log
                yield f"data: {json.dumps({'run_id': run_id, 'type': 'log', 'log': safe_log[:2000]})}\n\n"
                last_log = current_log

            if current_status in ("succeeded", "failed", "bug_found", "cancelled"):
                yield f"data: {json.dumps({'run_id': run_id, 'type': 'done', 'status': current_status})}\n\n"
                break

            await asyncio.sleep(2)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/{run_id}/rerun", response_model=TaskRead)
async def rerun_run(run_id: str, db: DbSession, _: CurrentUser):
    """Re-run a previous test run."""
    task = await task_service.get(db, run_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Run not found")

    rerun_context = _rerun_context_from_task(task)
    target_url = (
        rerun_context.get("ui_seed_url")
        if rerun_context.get("input_type") == "url" and rerun_context.get("ui_seed_url")
        else task.target_url
    )

    new_task = await task_service.create(
        db,
        objective=task.objective,
        target_url=target_url,
        test_type=task.test_type,
        status=TaskStatus.QUEUED,
    )
    try:
        run_agent_task.delay(
            new_task.id,
            new_task.objective,
            new_task.target_url,
            test_type=normalize_agent_test_type(new_task.test_type, default="auto"),
            **rerun_context,
        )
    except Exception as e:
        logger.warning("Celery dispatch failed on rerun: %s", e)
    return new_task


@router.post("/{run_id}/interventions", response_model=TaskRead)
async def create_run_intervention(
    run_id: str,
    payload: RunInterventionCreate,
    db: DbSession,
    _: CurrentUser,
):
    """Create an assisted rerun with human-supplied setup/intervention context."""
    task = await task_service.get(db, run_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Run not found")

    supplemental = payload.supplemental_instructions.strip()
    if not supplemental:
        raise HTTPException(status_code=400, detail="supplemental_instructions is required")

    current_status = _status_value(task.status)
    if current_status in _INTERVENTION_ACTIVE_STATUSES:
        if not payload.cancel_current:
            raise HTTPException(
                status_code=400,
                detail="Run is still active. Set cancel_current=true to cancel it before creating an assisted rerun.",
            )
        task = await _cancel_active_task(db, task, "Run cancelled before assisted intervention rerun")
    elif current_status not in {"failed", "bug_found", "cancelled"}:
        raise HTTPException(
            status_code=400,
            detail="Assisted intervention rerun is only available for failed, bug-found, cancelled, queued, or running runs.",
        )

    rerun_context = _rerun_context_from_task(task)
    combined_setup = _append_intervention_instructions(
        rerun_context.get("setup_instructions") or rerun_context.get("login_instructions"),
        supplemental,
    )
    rerun_context["setup_instructions"] = combined_setup
    rerun_context["login_instructions"] = combined_setup
    target_url = _intervention_target_url(task, rerun_context)

    new_task = await task_service.create(
        db,
        objective=task.objective,
        target_url=target_url,
        test_type=task.test_type,
        status=TaskStatus.QUEUED,
    )
    await _seed_intervention_execution_log(
        db,
        new_task,
        source_run_id=run_id,
        rerun_context=rerun_context,
    )

    try:
        run_agent_task.delay(
            new_task.id,
            new_task.objective,
            new_task.target_url,
            test_type=normalize_agent_test_type(new_task.test_type, default="auto"),
            **rerun_context,
        )
    except Exception as e:
        logger.warning("Celery dispatch failed on assisted intervention rerun: %s", e)
    return new_task


@router.post("/{run_id}/cancel")
async def cancel_run(run_id: str, db: DbSession, _: CurrentUser):
    """Cancel a running test run."""
    task = await task_service.get(db, run_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Run not found")
    await _cancel_active_task(db, task, "Run cancelled by user")
    return {"message": "Run cancelled"}


@router.delete("/{run_id}")
async def delete_run(run_id: str, db: DbSession, _: CurrentUser):
    """Delete a test run."""
    task = await task_service.get(db, run_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Run not found")
    await db.delete(task)
    await db.commit()
    return {"message": "deleted"}


async def _persist_state(db, task, final_state: dict):
    """Persist agent state to task execution_log."""
    await persist_task_state(
        db,
        task,
        final_state,
        status=determine_final_status(final_state),
        refresh=True,
    )

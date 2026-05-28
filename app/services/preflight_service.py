from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from fastapi import HTTPException
from sqlalchemy import func, select

from app.config import settings
from app.core.redaction import (
    REDACTED_VALUE,
    is_sensitive_header,
    redact_sensitive_data,
    redact_sensitive_text,
)
from app.models.environment import Environment
from app.models.llm_provider import LLMProvider
from app.models.task import Task
from app.models.test_case import TestSuite
from app.services import auth_preflight_service
from app.services.api_auth import AuthResolution, has_auth_like_header
from app.services.task_service import normalize_agent_test_type

logger = logging.getLogger(__name__)

NEW_RUN_TEST_TYPES = {"api", "ui"}
_TARGET_MEMORY_SAMPLE_LIMIT = 100
_TARGET_MEMORY_URL_RE = re.compile(r"https?://[^\s<>\")\]]+")
_INTERVENTION_SETUP_TERMS = (
    "auth",
    "login",
    "token",
    "credential",
    "unauthorized",
    "forbidden",
    "401",
    "403",
    "captcha",
    "mfa",
    "otp",
    "cookie",
    "session",
    "账号",
    "密码",
    "登录",
    "鉴权",
    "认证",
    "授权",
    "验证码",
    "会话",
)
_HISTORY_ISSUE_STATUSES = {"failed", "bug_found"}


@dataclass(slots=True)
class RunPreflightDependencies:
    best_effort_worker_readiness: Callable[[], Awaitable[tuple[str, str, str | None]]] | None = None
    best_effort_reachability: Callable[[str], Awaitable[str]] | None = None
    redis_broker_reachable: Callable[[float], Awaitable[bool]] | None = None


def normalize_new_run_test_type(value: str | None) -> str:
    normalized = (value or "api").strip().lower()
    if normalized not in NEW_RUN_TEST_TYPES:
        allowed = ", ".join(sorted(NEW_RUN_TEST_TYPES))
        raise ValueError(f"New runs accept test_type values: {allowed}")
    return normalized


def expected_flow_for(input_type: str, test_type: str, has_base_url: bool = False) -> list[str]:
    if test_type == "api":
        return ["识别输入", "解析 API", "生成接口用例", "执行 API 测试", "生成报告"]
    if test_type == "ui":
        return ["识别入口", "准备浏览器上下文", "规划 UI 场景", "执行 UI 测试", "生成报告"]
    if input_type in ("swagger_url", "swagger_json", "swagger_yaml") and not has_base_url:
        return ["识别 Swagger", "解析 API", "生成接口用例", "执行 API 测试", "生成报告"]
    if input_type in ("swagger_url", "swagger_json", "swagger_yaml"):
        return [
            "识别 Swagger",
            "解析 API",
            "执行 API 测试",
            "如有 UI 入口则继续 UI 测试",
            "生成报告",
        ]
    return ["识别目标", "准备浏览器上下文", "规划 UI 场景", "执行 UI 测试", "生成报告"]


async def count_rows(db: Any, model: type[Any]) -> int:
    result = await db.execute(select(func.count()).select_from(model))
    return int(result.scalar_one())


async def count_default_planners(db: Any) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(LLMProvider)
        .where(
            LLMProvider.is_active.is_(True),
            LLMProvider.is_default_planner.is_(True),
        )
    )
    return int(result.scalar_one())


def environment_default_model_available() -> bool:
    return bool(settings.DEFAULT_OPENAI_API_KEY.strip())


async def best_effort_endpoint_count(source: str, input_type: str) -> int | None:
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


async def best_effort_api_profile(
    source: str, input_type: str, api_execution_policy: str
) -> dict[str, Any]:
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
        inferred_target_url = _extract_document_base_url(
            content, source_url=source if input_type == "swagger_url" else None
        ) or (_extract_base_url(source) if input_type == "swagger_url" else None)
        policy = _normalize_api_execution_policy(api_execution_policy)
        write_allowed = _policy_allows_write(policy)
        skipped_for_policy = sum(
            1
            for endpoint in endpoints
            if str(endpoint.get("method", "GET")).upper() in WRITE_API_METHODS
            and not write_allowed
        )
        return {
            "endpoint_count": len(endpoints),
            "auth_required_count": sum(
                1 for endpoint in endpoints if endpoint.get("auth_required")
            ),
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


async def best_effort_reachability(source: str) -> str:
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


async def redis_broker_reachable(timeout: float) -> bool:
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


async def best_effort_worker_readiness(
    redis_probe: Callable[[float], Awaitable[bool]] | None = None,
) -> tuple[str, str, str | None]:
    timeout = max(float(settings.PREFLIGHT_WORKER_TIMEOUT_SECONDS), 0.1)
    probe = redis_probe or redis_broker_reachable
    if not await probe(timeout):
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


def resolve_run_target_url(source: str, input_type: str, base_url: str | None = None) -> str:
    if input_type == "url":
        return source
    return (base_url or source).strip()


def preflight_readiness(checks: list[Any]) -> str:
    if any(check.status == "missing" for check in checks):
        return "blocked"
    if any(check.status == "warning" for check in checks):
        return "needs_review"
    return "ready"


def redact_url_for_preview(value: str) -> str:
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


def preflight_target_label(source: str, input_type: str, target_url: str) -> str:
    if input_type in ("swagger_json", "swagger_yaml") and target_url == source:
        return "粘贴的 OpenAPI 文档（运行时解析 Base URL）"
    return redact_url_for_preview(target_url or source)


def preflight_input_mode_label(input_type: str) -> str:
    return {
        "url": "网页 URL",
        "swagger_url": "Swagger/OpenAPI URL",
        "swagger_json": "Swagger/OpenAPI JSON",
        "swagger_yaml": "Swagger/OpenAPI YAML",
    }.get(input_type, input_type)


def preflight_test_mode_label(test_type: str) -> str:
    return {
        "auto": "自动编排",
        "api": "API 检查",
        "ui": "UI 巡检",
    }.get(test_type, test_type)


def preflight_objective_summary(payload: Any, input_type: str, test_type: str) -> str:
    objective = payload.objective.strip()
    if objective:
        return objective
    if test_type == "api" or input_type in ("swagger_url", "swagger_json", "swagger_yaml"):
        return "验证 API 契约、参数边界、鉴权路径和错误分支。"
    if test_type == "ui":
        return "巡检入口页面、关键导航、表单路径和可见错误。"
    return "由智能体根据输入自动识别 API/UI 路径并生成测试计划。"


def preflight_scope_summary(
    input_type: str,
    test_type: str,
    endpoint_count: int | None,
    estimated_executable_count: int | None,
    estimated_skipped_count: int | None,
) -> str:
    if test_type == "ui" or (test_type == "auto" and input_type == "url"):
        return "浏览器会从目标入口开始探索 UI 路径，并采集截图与执行证据。"

    endpoint_text = (
        "运行时解析接口范围" if endpoint_count is None else f"文档包含 {endpoint_count} 个端点"
    )
    if estimated_executable_count is None:
        return f"{endpoint_text}，执行前会继续规划可运行用例。"
    skipped_text = (
        f"，策略跳过 {estimated_skipped_count} 个变更接口" if estimated_skipped_count else ""
    )
    return f"{endpoint_text}，预计执行 {estimated_executable_count} 个接口{skipped_text}。"


def preflight_policy_summary(api_execution_policy: str) -> str:
    if api_execution_policy == "write_allowed":
        return "允许写入/变更请求；仅适合测试环境或明确可回滚的数据。"
    if api_execution_policy == "safe_with_auth":
        return "带鉴权只读；使用凭据执行只读接口，写入/变更接口仍会跳过。"
    return "安全只读；默认跳过 POST/PUT/PATCH/DELETE，避免误改真实数据。"


def preflight_safety_boundary(payload: Any, api_execution_policy: str) -> str:
    if payload.setup_instructions.strip():
        return "已提供前置说明/安全边界；预览不展开可能包含凭据的原文。"
    if api_execution_policy == "write_allowed":
        return "未提供额外安全说明；允许写入前建议补充测试账号、可写范围和清理规则。"
    return "未提供额外安全说明；本次主要依赖执行策略限制高风险动作。"


def preflight_auth_readiness(
    auth_required_count: int | None,
    supplied_auth: bool,
    auth_resolution: AuthResolution,
) -> str:
    header_name = auth_resolution.header_name or "Authorization"
    if auth_required_count:
        if supplied_auth and auth_resolution.strategy == "manual_header":
            return f"手动 Token/Header 已通过；运行时会注入 {header_name}，预览不展示值。"
        if auth_resolution.ok:
            return f"自动获取 Token 已通过；运行时会注入 {header_name}，预览不展示值。"
        if supplied_auth:
            return "已提供 Token/Header；预览不展示任何鉴权值。"
        return f"检测到 {auth_required_count} 个接口需要鉴权；启动前需要补齐 Token/Header 或自动获取信息。"
    if auth_resolution.ok or supplied_auth:
        return "已提供鉴权信息；本次未检测到文档声明强制鉴权，预览不展示值。"
    return "未检测到接口鉴权要求。"


def default_correction_action(check: Any) -> str:
    return {
        "provider": "前往模型与 Agent 配置模型后重新预检。",
        "planner": "在模型与 Agent 中设置默认 Planner 可让测试计划更稳定。",
        "runner": "确认前端/Worker 镜像已安装浏览器工具。",
        "reachability": "确认目标 URL、内网/VPN、Base URL 或代理路径是否正确。",
        "environment": "可以继续使用当前输入；建议后续保存为环境资产复用。",
        "auth": "补齐 Token/Header，或选择自动获取 Token 并填写登录信息。",
        "api_policy": "确认目标为测试环境后再允许写入/变更接口。",
        "source": "修正目标入口/API 文档，或切换测试模式后重新预检。",
        "worker": "启动 Redis 和 Celery Worker 后重新预检。",
    }.get(check.key, "确认该项后重新预检。")


def preflight_correction_prompts(checks: list[Any], warnings: list[str]) -> list[Any]:
    from app.api.v1.runs import RunPreflightCorrectionPrompt

    prompts = [
        RunPreflightCorrectionPrompt(
            key=check.key,
            label=check.label,
            status=check.status,
            detail=check.detail,
            action=check.action or default_correction_action(check),
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


def build_mission_preview(
    payload: Any,
    *,
    source: str,
    input_type: str,
    test_type: str,
    target_url: str,
    expected_flow: list[str],
    readiness: str,
    checks: list[Any],
    warnings: list[str],
    endpoint_count: int | None,
    auth_required_count: int | None,
    estimated_executable_count: int | None,
    estimated_skipped_count: int | None,
    api_execution_policy: str,
    supplied_auth: bool,
    auth_resolution: AuthResolution,
) -> Any:
    from app.api.v1.runs import RunPreflightMissionCounts, RunPreflightMissionPreview

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
        target=preflight_target_label(source, input_type, target_url),
        input_mode=preflight_input_mode_label(input_type),
        test_mode=preflight_test_mode_label(test_type),
        objective=preflight_objective_summary(payload, input_type, test_type),
        scope=preflight_scope_summary(
            input_type,
            test_type,
            endpoint_count,
            estimated_executable_count,
            estimated_skipped_count,
        ),
        execution_policy=preflight_policy_summary(api_execution_policy),
        safety_boundary=preflight_safety_boundary(payload, api_execution_policy),
        auth_readiness=preflight_auth_readiness(
            auth_required_count, supplied_auth, auth_resolution
        ),
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
        correction_prompts=preflight_correction_prompts(checks, warnings),
    )


def redact_url_for_memory(value: str) -> str:
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


def target_memory_text(value: Any, limit: int = 220) -> str:
    text = redact_sensitive_text(str(value or "").strip())
    text = _TARGET_MEMORY_URL_RE.sub(lambda match: redact_url_for_memory(match.group(0)), text)
    text = re.sub(
        r"([?&][A-Za-z0-9_.:-]+)=([^\s&#,;)}\]]+)",
        lambda match: f"{match.group(1)}={REDACTED_VALUE}",
        text,
    )
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}..."


def target_memory_url_parts(value: Any) -> dict[str, str | None]:
    text = str(value or "").strip()
    if not text:
        return {"host": None, "exact": None, "label": "Unknown target"}
    try:
        parsed = urlsplit(text)
    except Exception:
        safe_text = target_memory_text(text, 160)
        return {
            "host": None,
            "exact": safe_text.lower() if safe_text else None,
            "label": safe_text or "Unknown target",
        }
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
            "label": redact_url_for_memory(text),
        }
    safe_text = target_memory_text(text, 160)
    return {
        "host": None,
        "exact": safe_text.lower() if safe_text else None,
        "label": safe_text or "Unknown target",
    }


def target_memory_task_matches(target_parts: dict[str, str | None], task: Task) -> bool:
    task_parts = target_memory_url_parts(getattr(task, "target_url", None))
    if target_parts.get("host") and task_parts.get("host"):
        return target_parts["host"] == task_parts["host"]
    if target_parts.get("exact") and task_parts.get("exact"):
        return target_parts["exact"] == task_parts["exact"]
    return False


def target_memory_last_seen(current: datetime | None, candidate: datetime) -> datetime:
    if current is None or candidate > current:
        return candidate
    return current


def target_memory_add_blocker(
    stats: dict[str, dict[str, Any]],
    *,
    category: str,
    label: str,
    reason: str,
    created_at: datetime,
) -> None:
    detail = target_memory_text(reason, 220)
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
    entry["last_seen_dt"] = target_memory_last_seen(entry["last_seen_dt"], created_at)
    if entry["detail"] == detail or created_at >= entry["last_seen_dt"]:
        entry["detail"] = detail


def target_memory_confidence(
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


def target_memory_strategy(
    *,
    previous_run_count: int,
    last_status: str | None,
    recurring_themes: list[Any],
    known_blockers: list[Any],
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


async def build_preflight_target_memory(
    db: Any,
    *,
    source: str,
    input_type: str,
    target_url: str,
) -> Any:
    from app.api.v1.runs import (
        RunTargetMemory,
        RunTargetMemoryBlocker,
        RunTargetMemoryLastRun,
        RunTargetMemorySuite,
        RunTargetMemoryTheme,
    )

    target_parts = target_memory_url_parts(target_url)
    result = await db.execute(
        select(Task).order_by(Task.created_at.desc()).limit(_TARGET_MEMORY_SAMPLE_LIMIT)
    )
    sampled_tasks = list(result.scalars())
    matching_tasks = [
        task for task in sampled_tasks if target_memory_task_matches(target_parts, task)
    ]
    now = datetime.utcnow()
    matching_tasks.sort(key=lambda item: history_created_at(item, now), reverse=True)

    task_ids = [task.id for task in matching_tasks]
    reusable_suites: list[Any] = []
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
                    label=target_memory_text(suite.name, 80) or "Untitled suite",
                    case_count=case_count,
                )
            )

    theme_stats: dict[str, dict[str, Any]] = {}
    blocker_stats: dict[str, dict[str, Any]] = {}
    for task in matching_tasks:
        status = history_status(task)
        created_at = history_created_at(task, now)
        parsed = redact_sensitive_data(parse_execution_log_dict(getattr(task, "execution_log", None)))
        triage = build_run_triage_summary(status, parsed)

        setup_blocked, setup_reason = setup_intervention_signal(parsed)
        api_blocked, api_reason = api_intervention_signal(parsed)
        ui_blocked, ui_reason = ui_intervention_signal(parsed)
        if setup_blocked or ui_blocked:
            target_memory_add_blocker(
                blocker_stats,
                category="setup_auth",
                label="登录/前置阻塞",
                reason=setup_reason or ui_reason,
                created_at=created_at,
            )
        if api_blocked:
            target_memory_add_blocker(
                blocker_stats,
                category="api_auth",
                label="API 鉴权阻塞",
                reason=api_reason,
                created_at=created_at,
            )

        issue_run = status in _HISTORY_ISSUE_STATUSES or triage_int(
            triage.get("blocking_count")
        ) > 0
        if not issue_run:
            continue
        for finding in triage_list(triage.get("blocking_findings")):
            if not isinstance(finding, dict):
                continue
            title = target_memory_text(finding.get("title") or finding.get("description"), 180)
            if not title:
                continue
            category = history_theme_category(finding)
            theme_key = f"{category}:{history_normalize_theme(title)}"
            severity = triage_severity(finding.get("severity"))
            entry = theme_stats.setdefault(
                theme_key,
                {
                    "theme": title,
                    "category": category,
                    "count": 0,
                    "severity": severity,
                    "severity_rank": triage_severity_rank(severity),
                    "surfaces": set(),
                    "last_seen_dt": None,
                },
            )
            entry["count"] += 1
            entry["last_seen_dt"] = target_memory_last_seen(entry["last_seen_dt"], created_at)
            if triage_severity_rank(severity) > entry["severity_rank"]:
                entry["severity"] = severity
                entry["severity_rank"] = triage_severity_rank(severity)
            surface = target_memory_text(finding.get("surface"), 120)
            if surface:
                entry["surfaces"].add(surface)

    recurring_themes = [
        RunTargetMemoryTheme(
            theme=item["theme"],
            category=item["category"],
            count=item["count"],
            severity=item["severity"],
            surfaces=sorted(item["surfaces"])[:5],
            last_seen=history_iso(item["last_seen_dt"]),
            recommended_action=history_theme_action(
                item["category"], item["severity"], item["theme"]
            ),
        )
        for item in theme_stats.values()
        if item["count"] > 1
    ]
    recurring_themes.sort(
        key=lambda item: (item.count, triage_severity_rank(item.severity), item.last_seen or ""),
        reverse=True,
    )

    known_blockers = [
        RunTargetMemoryBlocker(
            category=item["category"],
            label=item["label"],
            count=item["count"],
            detail=item["detail"],
            last_seen=history_iso(item["last_seen_dt"]),
        )
        for item in blocker_stats.values()
    ]
    known_blockers.sort(key=lambda item: (item.count, item.last_seen or ""), reverse=True)

    target_run_count = sum(
        1
        for task in matching_tasks
        if target_parts.get("exact")
        and target_memory_url_parts(getattr(task, "target_url", None)).get("exact")
        == target_parts.get("exact")
    )
    host_run_count = sum(
        1
        for task in matching_tasks
        if target_parts.get("host")
        and target_memory_url_parts(getattr(task, "target_url", None)).get("host")
        == target_parts.get("host")
    )
    previous_run_count = host_run_count if target_parts.get("host") else target_run_count

    last_task = matching_tasks[0] if matching_tasks else None
    last_status = history_status(last_task) if last_task else None
    confidence, confidence_reason = target_memory_confidence(
        previous_run_count=previous_run_count,
        recurring_theme_count=len(recurring_themes),
        known_blocker_count=len(known_blockers),
        reusable_suite_count=len(reusable_suites),
    )
    target_label = target_memory_text(preflight_target_label(source, input_type, target_url), 180)
    return RunTargetMemory(
        target=target_label or str(target_parts.get("label") or "Unknown target"),
        previous_run_count=previous_run_count,
        target_run_count=target_run_count,
        host_run_count=host_run_count,
        last_run=RunTargetMemoryLastRun(
            run_id=last_task.id,
            status=last_status or "",
            test_type=normalize_agent_test_type(last_task.test_type, default="auto")
            if last_task
            else None,
            created_at=history_iso(history_created_at(last_task, now)) if last_task else None,
        )
        if last_task
        else None,
        recurring_failure_themes=recurring_themes[:5],
        known_blockers=known_blockers[:5],
        reusable_suite_count=len(reusable_suites),
        reusable_case_count=reusable_case_count,
        reusable_suites=reusable_suites[:5],
        suggested_strategy=target_memory_strategy(
            previous_run_count=previous_run_count,
            last_status=last_status,
            recurring_themes=recurring_themes,
            known_blockers=known_blockers,
            reusable_suite_count=len(reusable_suites),
        ),
        confidence=confidence,
        confidence_reason=confidence_reason,
    )


def parse_execution_log_dict(log: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(log or "{}")
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def triage_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def triage_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def triage_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def triage_text(value: Any, limit: int = 280) -> str:
    text = redact_sensitive_text(str(value or "").strip())
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}..."


def triage_severity_rank(severity: Any) -> int:
    return {
        "CRITICAL": 4,
        "HIGH": 3,
        "MEDIUM": 2,
        "LOW": 1,
    }.get(str(severity or "").upper(), 2)


def triage_severity(value: Any, default: str = "MEDIUM") -> str:
    severity = str(value or default).upper()
    return severity if severity in {"CRITICAL", "HIGH", "MEDIUM", "LOW"} else default


def build_run_triage_summary(status: str, parsed: dict[str, Any]) -> dict[str, Any]:
    from app.api.v1.runs import _build_run_triage_summary

    return _build_run_triage_summary(status, parsed)


def contains_any_term(value: Any, terms: tuple[str, ...]) -> bool:
    text = str(value or "").lower()
    return any(term in text for term in terms)


def intervention_reason_text(*values: Any, fallback: str) -> str:
    for value in values:
        text = triage_text(value, 260)
        if text:
            return text
    return fallback


def setup_intervention_signal(parsed: dict[str, Any]) -> tuple[bool, str]:
    setup_result = triage_dict(parsed.get("setup_result") or parsed.get("login_result"))
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
        or contains_any_term(reason, _INTERVENTION_SETUP_TERMS)
    )
    if not failed:
        return False, ""
    return True, intervention_reason_text(
        reason,
        parsed.get("last_error"),
        fallback="登录、鉴权或前置准备未通过，需要补充可执行上下文。",
    )


def api_intervention_signal(parsed: dict[str, Any]) -> tuple[bool, str]:
    api_result = triage_dict(parsed.get("api_execution_result"))
    for result in triage_list(api_result.get("results")):
        item = triage_dict(result)
        status_code = triage_int(item.get("status_code") or item.get("envelope_status_code"))
        reason = (
            item.get("skip_reason")
            or item.get("failure_reason")
            or item.get("error")
            or item.get("category")
            or item.get("label")
        )
        if item.get("skipped") and contains_any_term(reason, _INTERVENTION_SETUP_TERMS):
            return True, intervention_reason_text(
                reason,
                fallback="API 用例因鉴权或上下文不足被跳过，需要补充 Token/Header 或登录信息。",
            )
        if status_code in {401, 403}:
            return True, intervention_reason_text(
                reason,
                fallback=f"API 返回 {status_code}，需要补充可用鉴权信息后重跑。",
            )

    skipped = triage_int(api_result.get("skipped"))
    executed = triage_int(api_result.get("executed") or api_result.get("completed"))
    if skipped and not executed:
        return True, "API 执行全部跳过，通常需要补充鉴权、Base URL、环境或可执行范围说明。"
    return False, ""


def ui_intervention_signal(parsed: dict[str, Any]) -> tuple[bool, str]:
    ui_result = triage_dict(parsed.get("ui_execution_result"))
    for case in triage_list(ui_result.get("cases")):
        item = triage_dict(case)
        reason = (
            item.get("skip_reason")
            or item.get("failure_reason")
            or item.get("error")
            or item.get("reason")
        )
        status = str(item.get("status") or "").lower()
        if status in {"skipped", "failed", "blocked"} and contains_any_term(
            reason, _INTERVENTION_SETUP_TERMS
        ):
            return True, intervention_reason_text(
                reason,
                fallback="UI 用例因登录、鉴权或前置状态不足未执行，需要补充测试账号和路径说明。",
            )

    last_error = ui_result.get("last_error") or ui_result.get("error")
    if contains_any_term(last_error, _INTERVENTION_SETUP_TERMS):
        return True, intervention_reason_text(
            last_error,
            fallback="UI 执行出现登录、鉴权或前置状态问题，需要补充上下文。",
        )
    return False, ""


def history_status(task: Any) -> str:
    status = getattr(task, "status", "")
    return status.value if hasattr(status, "value") else str(status)


def history_created_at(task: Any, fallback: datetime) -> datetime:
    value = getattr(task, "created_at", None)
    return value if isinstance(value, datetime) else fallback


def history_iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def history_theme_category(finding: dict[str, Any]) -> str:
    source = str(finding.get("source") or "").lower()
    if source == "api":
        return "api"
    if source.startswith("ui"):
        return "ui"
    return "report"


def history_normalize_theme(value: Any) -> str:
    text = triage_text(value, 220).lower()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\b\d{2,}\b", " # ", text)
    text = re.sub(r"[/_?=&.,:;()\\[\\]{}\"']+", " ", text)
    tokens = [
        token
        for token in text.split()
        if token
        and token
        not in {
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


def history_theme_action(category: str, severity: str, theme: str) -> str:
    if severity in {"CRITICAL", "HIGH"}:
        return f"优先修复反复出现的 {theme}，修复后重跑受影响范围。"
    if category == "api":
        return "复核接口断言、状态码和业务错误码，补充稳定的回归用例。"
    if category == "ui":
        return "复核页面状态、权限和选择器稳定性，保留截图证据后重跑。"
    return "整理缺陷证据和复现步骤，归并同类问题后逐项关闭。"


async def build_run_preflight_response(
    payload: Any,
    db: Any,
    *,
    deps: RunPreflightDependencies | None = None,
) -> Any:
    from app.agent.nodes.api_runner import _normalize_api_execution_policy
    from app.agent.nodes.source_loader import classify_input
    from app.api.v1.runs import RunPreflightCheck, RunPreflightResponse

    deps = deps or RunPreflightDependencies()
    source = payload.source.strip()
    if not source:
        raise HTTPException(status_code=400, detail="source is required")

    try:
        agent_test_type = normalize_new_run_test_type(payload.test_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    input_type = classify_input(source)
    api_execution_policy = _normalize_api_execution_policy(payload.api_execution_policy)
    target_url = resolve_run_target_url(source, input_type, payload.base_url)
    provider_count = await count_rows(db, LLMProvider)
    planner_count = await count_default_planners(db)
    fallback_model_available = environment_default_model_available()
    any_model_available = bool(provider_count or fallback_model_available)
    planner_available = bool(planner_count or fallback_model_available)
    environment_count = await count_rows(db, Environment)
    api_profile = await best_effort_api_profile(source, input_type, api_execution_policy)
    endpoint_count = api_profile["endpoint_count"]
    auth_required_count = api_profile["auth_required_count"]
    estimated_executable_count = api_profile["estimated_executable_count"]
    estimated_skipped_count = api_profile["estimated_skipped_count"]
    if not payload.base_url and api_profile.get("target_url"):
        target_url = str(api_profile["target_url"])
    reachability_probe = deps.best_effort_reachability or best_effort_reachability
    reachability = await reachability_probe(source)
    worker_probe = deps.best_effort_worker_readiness
    if worker_probe is not None:
        worker_status, worker_detail, worker_action = await worker_probe()
    else:
        worker_status, worker_detail, worker_action = await best_effort_worker_readiness(
            deps.redis_broker_reachable
        )
    browser_tool_found = (
        shutil.which("playwright-cli") is not None or shutil.which("npx") is not None
    )
    (
        auth_preflight,
        prepared_headers,
        runtime_auth_config,
        auth_resolution,
    ) = await auth_preflight_service.run_auth_preflight(
        payload,
        db=db,
        source=source,
        input_type=input_type,
        target_url=target_url,
        test_type=agent_test_type,
        auth_required_count=auth_required_count,
    )
    auth_preflight_service.cache_auth_preflight(
        fingerprint=auth_preflight_service.auth_preflight_fingerprint(payload),
        auth_preflight=auth_preflight,
        auth_headers=prepared_headers,
        runtime_auth_config=runtime_auth_config,
        auth_resolution=auth_resolution,
    )
    supplied_auth = auth_preflight.can_start or has_auth_like_header(prepared_headers)
    auth_mode = auth_preflight_service.normalize_auth_mode(payload)
    auth_attempted = auth_mode == "auto"

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
            status="ready" if any_model_available else "missing",
            detail="已配置可用模型"
            if provider_count
            else "已检测到环境变量默认 OpenAI 兼容模型"
            if fallback_model_available
            else "尚未配置 LLM Provider；Agent 无法进行模型驱动的规划、评估和重规划",
            action=None
            if any_model_available
            else "在模型与 Agent 中添加可用模型并设置默认 Planner",
        ),
        RunPreflightCheck(
            key="planner",
            label="规划模型",
            status="ready" if planner_available else "missing",
            detail="已有默认 Planner 模型"
            if planner_count
            else "将使用环境变量 DEFAULT_MODEL_PLANNER 作为默认 Planner"
            if fallback_model_available
            else "未设置默认 Planner，不能启动 Agent Run，避免退化为固定规则执行",
            action=None if planner_available else "在模型与 Agent 中设置默认 Planner",
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
            detail="检测到本地浏览器执行入口"
            if browser_tool_found
            else "未检测到 playwright-cli 或 npx，UI 测试可能失败",
            action=None if browser_tool_found else "确认前端/Worker 镜像已安装浏览器工具",
        ),
        RunPreflightCheck(
            key="reachability",
            label="目标可达性",
            status=reachability,
            detail="目标可访问"
            if reachability == "ready"
            else "未执行网络检查"
            if reachability == "skipped"
            else "暂时无法确认目标可达",
        ),
        RunPreflightCheck(
            key="environment",
            label="环境资产",
            status="ready" if environment_count else "warning",
            detail=f"已配置 {environment_count} 个环境"
            if environment_count
            else "尚未沉淀环境配置，本次将使用输入源直接运行",
        ),
        RunPreflightCheck(
            key="auth",
            label="鉴权准备",
            status="ready"
            if auth_preflight.can_start
            else "missing"
            if auth_preflight.status == "blocked"
            else "warning",
            detail=(auth_preflight.steps[-1].detail if auth_preflight.steps else "鉴权预检未完成"),
            action=(
                None
                if auth_preflight.can_start
                else auth_preflight.next_action
                or "选择自动鉴权、手动 Header/Token，或确认无需鉴权。"
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
        warnings.append(
            "原文 Swagger 未提供 Base URL 时，系统只能依赖文档 servers 字段推断请求地址。"
        )
    if agent_test_type == "ui" and not payload.setup_instructions.strip():
        warnings.append(
            "如果目标需要登录、验证码或其他前置步骤，请在前置说明里提供测试账号和安全边界。"
        )
    if input_type == "url" and agent_test_type == "api":
        warnings.append(
            "当前输入看起来是网页 URL，但测试模式选择了 API；建议确认是否应使用 Swagger/OpenAPI。"
        )
    if not auth_preflight.can_start:
        warnings.append(f"鉴权预检未通过：{auth_preflight.next_action or auth_preflight.status}")
    if auth_attempted and auth_resolution.detail and not auth_resolution.ok:
        warnings.append(f"自动鉴权预检失败：{auth_resolution.detail}")
    if estimated_skipped_count:
        warnings.append(
            f"当前 API 策略预计会跳过 {estimated_skipped_count} 个写入/变更接口，避免误改真实数据。"
        )
    if api_profile.get("api_path_prefix_rewrite"):
        rewrite = api_profile["api_path_prefix_rewrite"]
        warnings.append(
            f"检测到代理路径改写：请求执行时会将 {rewrite.get('from')} 改为 {rewrite.get('to')}。"
        )

    expected_flow = expected_flow_for(input_type, agent_test_type, bool(payload.base_url))
    readiness = preflight_readiness(checks)
    mission_preview = build_mission_preview(
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
    target_memory = await build_preflight_target_memory(
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
        auth_preflight=auth_preflight,
        warnings=warnings,
        endpoint_count=endpoint_count,
        auth_required_count=auth_required_count,
        estimated_executable_count=estimated_executable_count,
        estimated_skipped_count=estimated_skipped_count,
        api_execution_policy=api_execution_policy,
        api_path_prefix_rewrite=api_profile.get("api_path_prefix_rewrite"),
        auth_resolved=auth_resolution.ok
        or (auth_preflight.can_start and auth_preflight.strategy == "manual_header"),
        auth_strategy=auth_resolution.strategy or auth_preflight.strategy,
        auth_header_name=auth_resolution.header_name,
        auth_error=None
        if auth_preflight.can_start
        else auth_resolution.detail or auth_preflight.next_action,
        auth_missing_inputs=[]
        if auth_preflight.can_start
        else auth_preflight.missing_fields or auth_resolution.missing_inputs,
        auth_next_action=None
        if auth_preflight.can_start
        else auth_preflight.next_action or auth_resolution.next_action,
        auth_required_fields=[]
        if auth_preflight.can_start
        else auth_resolution.required_fields or auth_preflight.missing_fields,
    )

import asyncio
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
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
)
from app.database import AsyncSessionLocal
from app.models.environment import Environment
from app.models.llm_provider import LLMProvider
from app.models.task import TaskStatus, TestType
from app.schemas.task import TaskRead, parse_task_detail
from app.services.api_auth import (
    AuthResolution,
    coerce_auth_config,
    has_auth_like_header,
    merge_token_header,
    normalize_headers,
    resolve_auto_auth_headers,
)
from app.services.task_service import normalize_agent_test_type, normalize_test_type, task_service
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


class RunPreflightResponse(BaseModel):
    input_type: str
    test_type: str
    target_url: str
    expected_flow: list[str]
    readiness: str
    checks: list[RunPreflightCheck]
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


def _parse_execution_log_dict(log: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(log or "{}")
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


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
        if not value_text or value_text == REDACTED_VALUE:
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

    return RunPreflightResponse(
        input_type=input_type,
        test_type=agent_test_type,
        target_url=target_url,
        expected_flow=_expected_flow_for(input_type, agent_test_type, bool(payload.base_url)),
        readiness=_preflight_readiness(checks),
        checks=checks,
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


@router.get("", response_model=list[TaskRead])
async def list_runs(
    db: DbSession, _: CurrentUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: str | None = Query(default=None),
    test_type: str | None = Query(default=None),
):
    """List all test runs with optional filters."""
    try:
        items, total = await task_service.list(
            db, page=page, page_size=page_size, status=status, test_type=test_type
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(
        content=[TaskRead.model_validate(i).model_dump(mode="json") for i in items],
        headers={"X-Total-Count": str(total)},
    )


@router.get("/{run_id}")
async def get_run_detail(run_id: str, db: DbSession, _: CurrentUser):
    """Get full run detail including plan, cases, API results, UI results, screenshots, summary."""
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

    return detail


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


@router.post("/{run_id}/cancel")
async def cancel_run(run_id: str, db: DbSession, _: CurrentUser):
    """Cancel a running test run."""
    task = await task_service.get(db, run_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Run not found")
    current_status = task.status if isinstance(task.status, str) else task.status.value
    if current_status not in ("queued", "running"):
        raise HTTPException(status_code=400, detail="Run is not in a cancellable state")
    try:
        from app.worker.celery_app import celery_app
        celery_app.control.revoke(run_id, terminate=True)
    except Exception as e:
        logger.warning("Celery revoke failed for run %s: %s", run_id, e)
    await mark_task_cancelled(db, task, "Run cancelled by user")
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

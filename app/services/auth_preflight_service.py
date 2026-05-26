from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from sqlalchemy import func, select

from app.core.redaction import REDACTED_VALUE, is_sensitive_header, redact_sensitive_data
from app.models.llm_provider import LLMProvider
from app.services.api_auth import (
    AuthResolution,
    CaptchaContextResolution,
    captcha_required_by_login,
    coerce_auth_config,
    fetch_captcha_context,
    has_auth_like_header,
    load_auth_endpoints,
    login_endpoint_for_config,
    merge_token_header,
    normalize_headers,
    resolve_auto_auth_headers,
)

logger = logging.getLogger(__name__)

AUTH_MODES = {"auto", "manual", "none_confirmed"}
CAPTCHA_MODES = {"none", "static", "dynamic"}
AUTH_PREFLIGHT_CACHE_TTL_SECONDS = 10 * 60
AUTH_PREFLIGHT_VALID_STATUSES = {"passed", "warning"}
_AUTH_PREFLIGHT_CACHE: dict[str, dict[str, Any]] = {}


async def prepare_run_auth(
    payload: Any,
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


def normalize_auth_mode(payload: Any) -> str:
    mode = (payload.auth_mode or "auto").strip().lower()
    if mode not in AUTH_MODES:
        mode = "auto"
    if mode == "auto":
        legacy_headers = normalize_headers(payload.headers)
        merge_token_header(payload.token, legacy_headers)
        auth_config = coerce_auth_config(payload.auth_config)
        if (
            has_auth_like_header(legacy_headers)
            and not auth_config.get("enabled")
            and not has_login_credentials(payload)
        ):
            return "manual"
    return mode


def normalize_captcha_mode(payload: Any) -> str:
    mode = (payload.captcha_mode or "none").strip().lower()
    return mode if mode in CAPTCHA_MODES else "none"


def auth_credentials_dict(payload: Any) -> dict[str, str]:
    credentials = payload.auth_credentials
    data: dict[str, str] = {}
    if credentials is not None:
        for key in ("username", "password", "captcha"):
            value = getattr(credentials, key, None)
            if isinstance(value, str) and value.strip():
                data[key] = value.strip()
    config = coerce_auth_config(payload.auth_config)
    for key in ("username", "password", "captcha", "tenant"):
        value = config.get(key)
        if isinstance(value, str) and value.strip() and key not in data:
            data[key] = value.strip()
    return data


def auth_config_with_credentials(
    payload: Any,
    *,
    enabled: bool,
    captcha_text: str | None = None,
) -> dict[str, Any]:
    config = coerce_auth_config(payload.auth_config)
    credentials = auth_credentials_dict(payload)
    for key in ("username", "password", "captcha", "tenant"):
        if credentials.get(key) and not config.get(key):
            config[key] = credentials[key]
    if captcha_text:
        config["captcha"] = captcha_text
    if enabled:
        config["enabled"] = True
    return config


def has_login_credentials(payload: Any) -> bool:
    credentials = auth_credentials_dict(payload)
    if credentials.get("username") and credentials.get("password"):
        return True
    config = coerce_auth_config(payload.auth_config)
    body = config.get("body")
    if isinstance(body, dict) and body:
        return True
    return False


def auth_preflight_fingerprint(payload: Any) -> str:
    data = {
        "source": payload.source,
        "test_type": payload.test_type,
        "objective": payload.objective,
        "base_url": payload.base_url,
        "headers": payload.headers,
        "token": payload.token,
        "auth_mode": normalize_auth_mode(payload),
        "captcha_mode": normalize_captcha_mode(payload),
        "auth_credentials": payload.auth_credentials.model_dump(mode="json", exclude_none=True)
        if payload.auth_credentials
        else None,
        "auth_config": payload.auth_config.model_dump(mode="json", exclude_none=True)
        if payload.auth_config
        else None,
        "api_execution_policy": payload.api_execution_policy,
        "setup_instructions": payload.setup_instructions,
        "login_instructions": getattr(payload, "login_instructions", ""),
    }
    if not data["setup_instructions"] and data["login_instructions"]:
        data["setup_instructions"] = data["login_instructions"]
    data.pop("login_instructions", None)
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def cache_auth_preflight(
    *,
    fingerprint: str,
    auth_preflight: Any,
    auth_headers: dict[str, str],
    runtime_auth_config: dict[str, Any] | None,
    auth_resolution: AuthResolution,
) -> str:
    auth_preflight_id = str(uuid.uuid4())
    auth_preflight.auth_preflight_id = auth_preflight_id
    _AUTH_PREFLIGHT_CACHE[auth_preflight_id] = {
        "created_at": time.time(),
        "fingerprint": fingerprint,
        "auth_preflight": auth_preflight,
        "auth_headers": dict(auth_headers),
        "runtime_auth_config": runtime_auth_config,
        "auth_resolution": auth_resolution,
    }
    return auth_preflight_id


def get_cached_auth_preflight(
    payload: Any,
) -> tuple[Any, dict[str, str], dict[str, Any] | None, AuthResolution] | None:
    from app.api.v1.runs import RunAuthPreflight

    preflight_id = (payload.auth_preflight_id or "").strip()
    if not preflight_id:
        return None
    cached = _AUTH_PREFLIGHT_CACHE.get(preflight_id)
    if not cached:
        return None
    if time.time() - float(cached.get("created_at") or 0) > AUTH_PREFLIGHT_CACHE_TTL_SECONDS:
        _AUTH_PREFLIGHT_CACHE.pop(preflight_id, None)
        return None
    if cached.get("fingerprint") != auth_preflight_fingerprint(payload):
        return None
    auth_preflight = cached.get("auth_preflight")
    if not isinstance(auth_preflight, RunAuthPreflight):
        return None
    if auth_preflight.status not in AUTH_PREFLIGHT_VALID_STATUSES or not auth_preflight.can_start:
        return None
    auth_resolution = cached.get("auth_resolution")
    if not isinstance(auth_resolution, AuthResolution):
        auth_resolution = AuthResolution(ok=False, detail="No cached auth resolution")
    return (
        auth_preflight,
        dict(cached.get("auth_headers") or {}),
        cached.get("runtime_auth_config"),
        auth_resolution,
    )


async def load_preflight_endpoints(source: str, input_type: str) -> list[dict[str, Any]]:
    if input_type == "url":
        return []
    try:
        _, endpoints = await load_auth_endpoints(source, input_type)
        return endpoints
    except Exception as exc:
        logger.debug("Unable to load auth preflight endpoints: %s", exc)
        return []


def api_validation_candidates(
    endpoints: list[dict[str, Any]],
    target_url: str,
    *,
    protected_only: bool,
    limit: int = 3,
) -> list[dict[str, str]]:
    from app.agent.nodes.api_runner import (
        SAFE_API_METHODS,
        _build_request_url,
        _resolve_path_params,
    )

    candidates: list[dict[str, str]] = []
    for endpoint in endpoints:
        method = str(endpoint.get("method") or "GET").upper()
        if method not in SAFE_API_METHODS:
            continue
        if protected_only and not endpoint.get("auth_required"):
            continue
        path = _resolve_path_params(str(endpoint.get("path") or ""), endpoint)
        url = _build_request_url(target_url, path)
        if not url:
            continue
        candidates.append({"method": method, "url": url, "path": path})
        if len(candidates) >= limit:
            break
    return candidates


def is_preflight_auth_failure(status_code: int, payload: Any) -> bool:
    if status_code in {401, 403}:
        return True
    if isinstance(payload, dict):
        for key in ("code", "status", "status_code"):
            value = payload.get(key)
            try:
                if int(value) in {401, 403}:
                    return True
            except (TypeError, ValueError):
                continue
    return False


async def validate_readonly_auth_access(
    candidates: list[dict[str, str]],
    headers: dict[str, str],
) -> tuple[list[Any], int]:
    from app.api.v1.runs import RunAuthPreflightValidation

    results: list[Any] = []
    success_count = 0
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
        for candidate in candidates:
            method = candidate["method"]
            url = candidate["url"]
            try:
                response = await client.request(method, url, headers=headers or None)
                try:
                    payload = response.json()
                except ValueError:
                    payload = {}
                auth_failed = is_preflight_auth_failure(response.status_code, payload)
                ok = 200 <= response.status_code < 400 and not auth_failed
                if ok:
                    success_count += 1
                status_text = "passed" if ok else "failed"
                detail = "只读接口验证通过" if ok else "只读接口验证未通过"
                if auth_failed:
                    detail = "只读接口仍返回 401/403"
                results.append(
                    RunAuthPreflightValidation(
                        method=method,
                        url=redact_url_for_preview(url),
                        status=status_text,
                        status_code=response.status_code,
                        detail=detail,
                    )
                )
            except Exception as exc:
                safe_detail = redact_sensitive_data(str(exc)) if str(exc) else "未知错误"
                results.append(
                    RunAuthPreflightValidation(
                        method=method,
                        url=redact_url_for_preview(url),
                        status="failed",
                        status_code=None,
                        detail=f"只读接口验证请求失败：{safe_detail[:120]}",
                    )
                )
    return results, success_count


def auth_preflight_response(
    *,
    auth_mode: str,
    captcha_mode: str,
    test_type: str,
    status: str,
    strategy: str,
    plan: str,
    captcha_handling: str,
    steps: list[Any],
    missing_fields: list[str] | None = None,
    validation_results: list[Any] | None = None,
    auth_header_name: str | None = None,
    protected_validation_count: int = 0,
    next_action: str | None = None,
) -> Any:
    from app.api.v1.runs import RunAuthPreflight

    can_start = status in AUTH_PREFLIGHT_VALID_STATUSES
    if test_type == "api" and validation_results is not None:
        can_start = can_start and (protected_validation_count > 0 or auth_mode == "none_confirmed")
    return RunAuthPreflight(
        auth_mode=auth_mode,
        captcha_mode=captcha_mode,
        status=status,
        strategy=strategy,
        plan=plan,
        captcha_handling=captcha_handling,
        steps=steps,
        missing_fields=missing_fields or [],
        validation_results=validation_results or [],
        auth_header_name=auth_header_name,
        protected_validation_count=protected_validation_count,
        can_start=can_start,
        next_action=next_action,
    )


def captcha_context_summary(captcha: CaptchaContextResolution | None) -> str:
    if captcha is None:
        return "无验证码"
    if not captcha.ok:
        return captcha.detail or "验证码上下文获取失败"
    fields = [key for key in captcha.context.keys() if key not in {"status_code"}]
    if captcha.captcha_text:
        return f"已获取验证码上下文（{len(fields)} 个字段），接口返回了明文验证码。"
    return f"已获取验证码上下文（{len(fields)} 个字段）；接口测试不会识别图片验证码。"


async def run_auth_preflight(
    payload: Any,
    *,
    db: Any,
    source: str,
    input_type: str,
    target_url: str,
    test_type: str,
    auth_required_count: int | None,
) -> tuple[Any, dict[str, str], dict[str, Any] | None, AuthResolution]:
    from app.api.v1.runs import RunAuthPreflightStep

    auth_mode = normalize_auth_mode(payload)
    captcha_mode = normalize_captcha_mode(payload)
    endpoints = await load_preflight_endpoints(source, input_type)
    headers = normalize_headers(payload.headers)
    merge_token_header(payload.token, headers)
    runtime_auth_config: dict[str, Any] | None = None
    auth_resolution = AuthResolution(ok=False, detail="未执行自动鉴权")
    steps: list[Any] = [
        RunAuthPreflightStep(
            key="mode",
            label="鉴权模式",
            status="passed",
            detail={
                "auto": "自动获取 Token",
                "manual": "手动 Token/Header",
                "none_confirmed": "无需鉴权",
            }[auth_mode],
        )
    ]

    if test_type == "ui":
        vision_count = await count_default_vision_models(db)
        if captcha_mode == "dynamic" and not vision_count:
            steps.append(
                RunAuthPreflightStep(
                    key="captcha",
                    label="动态验证码",
                    status="blocked",
                    detail="UI 动态验证码需要默认 Vision 模型，当前未配置。",
                )
            )
            auth_preflight = auth_preflight_response(
                auth_mode=auth_mode,
                captcha_mode=captcha_mode,
                test_type=test_type,
                status="blocked",
                strategy="ui_browser_login",
                plan="浏览器打开登录页，由页面结构和测试账号推理登录步骤；动态验证码由 Vision 模型识别。",
                captcha_handling="动态验证码：运行时使用 Vision 模型识别页面验证码图片。",
                steps=steps,
                missing_fields=["vision_model"],
                next_action="在模型与 Agent 中设置默认 Vision 模型，或改用固定验证码/无验证码。",
            )
            return auth_preflight, headers, None, auth_resolution
        if auth_mode == "manual" and not has_auth_like_header(headers):
            steps.append(
                RunAuthPreflightStep(
                    key="manual_auth",
                    label="手动鉴权",
                    status="blocked",
                    detail="手动模式需要提供 Token 或鉴权 Header。",
                )
            )
            auth_preflight = auth_preflight_response(
                auth_mode=auth_mode,
                captcha_mode=captcha_mode,
                test_type=test_type,
                status="blocked",
                strategy="ui_manual_header",
                plan="浏览器测试将使用手动凭据作为上下文，但启动前需要先提供凭据。",
                captcha_handling="UI 手动模式不处理验证码，除非同时提供登录说明。",
                steps=steps,
                missing_fields=["token_or_header"],
                next_action="填写 Token/Header，或切换到自动获取 Token。",
            )
            return auth_preflight, headers, None, auth_resolution
        if auth_mode == "auto" and not has_login_credentials(payload):
            steps.append(
                RunAuthPreflightStep(
                    key="credentials",
                    label="登录凭据",
                    status="blocked",
                    detail="自动获取 Token 需要账号和密码；如果页面无需登录，请选择无需鉴权。",
                )
            )
            auth_preflight = auth_preflight_response(
                auth_mode=auth_mode,
                captcha_mode=captcha_mode,
                test_type=test_type,
                status="blocked",
                strategy="ui_browser_login",
                plan="浏览器打开登录页，由页面结构和测试账号推理登录步骤。",
                captcha_handling="验证码策略会在 UI 登录步骤中执行。",
                steps=steps,
                missing_fields=["username", "password"],
                next_action="填写账号密码，或选择确认无需鉴权。",
            )
            return auth_preflight, headers, None, auth_resolution
        steps.append(
            RunAuthPreflightStep(
                key="ui_runtime_verify",
                label="登录后页面验证",
                status="passed",
                detail="UI 执行前会验证已进入登录后页面；验证失败不会继续 UI 用例。",
            )
        )
        auth_preflight = auth_preflight_response(
            auth_mode=auth_mode,
            captcha_mode=captcha_mode,
            test_type=test_type,
            status="passed",
            strategy="ui_browser_login" if auth_mode == "auto" else auth_mode,
            plan="浏览器打开目标入口，模型根据页面结构推理登录链路并执行；登录后验证页面状态。",
            captcha_handling={
                "none": "无验证码。",
                "static": "固定验证码：使用用户填写的验证码。",
                "dynamic": "动态验证码：运行时使用默认 Vision 模型识别页面验证码图片。",
            }[captcha_mode],
            steps=steps,
            auth_header_name=None,
            protected_validation_count=0,
            next_action=None,
        )
        return auth_preflight, headers, None, auth_resolution

    protected_candidates = api_validation_candidates(
        endpoints,
        target_url,
        protected_only=True,
        limit=3,
    )
    any_read_candidates = api_validation_candidates(
        endpoints,
        target_url,
        protected_only=False,
        limit=3,
    )
    captcha_context: CaptchaContextResolution | None = None

    if auth_mode == "none_confirmed":
        candidates = protected_candidates or any_read_candidates
        if not candidates:
            steps.append(
                RunAuthPreflightStep(
                    key="readonly_validation",
                    label="无鉴权验证",
                    status="blocked",
                    detail="没有可用于确认无需鉴权的只读接口。",
                )
            )
            auth_preflight = auth_preflight_response(
                auth_mode=auth_mode,
                captcha_mode=captcha_mode,
                test_type=test_type,
                status="blocked",
                strategy="none_confirmed",
                plan="不注入任何鉴权信息，仅验证只读接口无鉴权可访问。",
                captcha_handling="无验证码。",
                steps=steps,
                missing_fields=["read_only_endpoint"],
                next_action="提供 OpenAPI 中可访问的 GET/HEAD/OPTIONS 接口，或改用自动/手动鉴权。",
            )
            return auth_preflight, {}, None, auth_resolution
        validation_results, success_count = await validate_readonly_auth_access(candidates, {})
        status = "passed" if success_count >= max(1, min(2, len(candidates))) else "blocked"
        steps.append(
            RunAuthPreflightStep(
                key="readonly_validation",
                label="无鉴权验证",
                status=status,
                detail=f"无鉴权只读验证通过 {success_count}/{len(candidates)} 个接口。",
            )
        )
        auth_preflight = auth_preflight_response(
            auth_mode=auth_mode,
            captcha_mode=captcha_mode,
            test_type=test_type,
            status=status,
            strategy="none_confirmed",
            plan="不注入任何鉴权信息，仅在只读接口确认可访问后启动。",
            captcha_handling="无验证码。",
            steps=steps,
            validation_results=validation_results,
            protected_validation_count=success_count,
            next_action=None
            if status == "passed"
            else "目标仍需要鉴权；请选择自动鉴权或手动 Header/Token。",
        )
        return auth_preflight, {}, None, auth_resolution

    if auth_mode == "manual":
        if not has_auth_like_header(headers):
            steps.append(
                RunAuthPreflightStep(
                    key="manual_auth",
                    label="手动鉴权",
                    status="blocked",
                    detail="手动模式需要提供 Token 或鉴权 Header。",
                )
            )
            auth_preflight = auth_preflight_response(
                auth_mode=auth_mode,
                captcha_mode=captcha_mode,
                test_type=test_type,
                status="blocked",
                strategy="manual_header",
                plan="使用用户提供的 Header/Token 调用受保护只读接口。",
                captcha_handling="手动 Header/Token 模式不处理验证码。",
                steps=steps,
                missing_fields=["token_or_header"],
                next_action="填写 Token/Header 后重新预检。",
            )
            return auth_preflight, headers, None, auth_resolution
        candidates = protected_candidates
        if not candidates:
            steps.append(
                RunAuthPreflightStep(
                    key="protected_validation",
                    label="受保护接口验证",
                    status="blocked",
                    detail="没有可用于验证手动鉴权的受保护只读接口。",
                )
            )
            auth_preflight = auth_preflight_response(
                auth_mode=auth_mode,
                captcha_mode=captcha_mode,
                test_type=test_type,
                status="blocked",
                strategy="manual_header",
                plan="使用用户提供的 Header/Token 调用受保护只读接口。",
                captcha_handling="手动 Header/Token 模式不处理验证码。",
                steps=steps,
                missing_fields=["protected_read_only_endpoint"],
                auth_header_name="Authorization" if (payload.token or "").strip() else None,
                next_action="补充 OpenAPI 中带 security 的 GET/HEAD/OPTIONS 接口，或改用可验证的登录链路。",
            )
            return auth_preflight, headers, None, auth_resolution
        validation_results, success_count = await validate_readonly_auth_access(
            candidates, headers
        )
        status = "passed" if success_count >= max(1, min(2, len(candidates))) else "blocked"
        steps.append(
            RunAuthPreflightStep(
                key="protected_validation",
                label="受保护接口验证",
                status=status,
                detail=f"手动鉴权只读验证通过 {success_count}/{len(candidates)} 个接口。",
            )
        )
        runtime_auth_config = coerce_auth_config(payload.auth_config)
        if not runtime_auth_config.get("enabled"):
            runtime_auth_config = None
        auth_preflight = auth_preflight_response(
            auth_mode=auth_mode,
            captcha_mode=captcha_mode,
            test_type=test_type,
            status=status,
            strategy="manual_header",
            plan="使用用户提供的 Header/Token 调用受保护只读接口，通过后再启动接口测试。",
            captcha_handling="手动 Header/Token 模式不处理验证码。",
            steps=steps,
            validation_results=validation_results,
            auth_header_name="Authorization" if (payload.token or "").strip() else None,
            protected_validation_count=success_count,
            next_action=None
            if status == "passed"
            else "确认 Token/Header 有效，并确保受保护只读接口可访问。",
        )
        return (
            auth_preflight,
            headers,
            runtime_auth_config,
            AuthResolution(
                ok=status == "passed",
                headers=headers,
                strategy="manual_header",
                header_name="Authorization" if (payload.token or "").strip() else None,
                detail="手动 Header/Token 预检通过"
                if status == "passed"
                else "手动 Header/Token 预检失败",
            ),
        )

    if not has_login_credentials(payload):
        configured_auto = coerce_auth_config(payload.auth_config)
        if configured_auto.get("enabled"):
            auth_resolution = await resolve_auto_auth_headers(
                configured_auto,
                source=source,
                input_type=input_type,
                target_url=target_url,
                endpoints=endpoints,
            )
            missing_fields = auth_resolution.missing_inputs or ["username", "password"]
            detail = auth_resolution.detail or "自动获取 Token 缺少登录凭据。"
            next_action = auth_resolution.next_action or "补齐登录凭据，或选择手动 Token/Header。"
            required_fields = auth_resolution.required_fields or missing_fields
        else:
            missing_fields = ["username", "password"]
            detail = "检测到鉴权预检未完成；必须提供 Token/Header、填写账号密码，或选择无需鉴权。"
            next_action = "填写账号密码，或选择手动 Token/Header / 无需鉴权。"
            required_fields = missing_fields
        steps.append(
            RunAuthPreflightStep(
                key="credentials",
                label="登录凭据",
                status="blocked",
                detail=detail,
            )
        )
        auth_preflight = auth_preflight_response(
            auth_mode=auth_mode,
            captcha_mode=captcha_mode,
            test_type=test_type,
            status="blocked",
            strategy="auto_login",
            plan="根据 OpenAPI 推理 login/token/captcha/csrf 链路，并限制为登录相关接口与只读验证接口。",
            captcha_handling="验证码策略尚未执行。",
            steps=steps,
            missing_fields=required_fields,
            next_action=next_action,
        )
        return auth_preflight, headers, None, auth_resolution

    auth_config = auth_config_with_credentials(payload, enabled=True)
    login_url, login_endpoint = login_endpoint_for_config(
        auth_config,
        endpoints=endpoints,
        target_url=target_url,
    )
    if captcha_mode == "static":
        captcha_value = (
            auth_credentials_dict(payload).get("captcha")
            or str(auth_config.get("captcha") or "").strip()
        )
        if not captcha_value:
            steps.append(
                RunAuthPreflightStep(
                    key="captcha",
                    label="固定验证码",
                    status="blocked",
                    detail="固定验证码模式需要填写验证码。",
                )
            )
            auth_preflight = auth_preflight_response(
                auth_mode=auth_mode,
                captcha_mode=captcha_mode,
                test_type=test_type,
                status="blocked",
                strategy="auto_login",
                plan="使用用户填写的固定验证码执行登录。",
                captcha_handling="固定验证码：缺少用户填写的验证码。",
                steps=steps,
                missing_fields=["captcha"],
                next_action="填写验证码后重新预检。",
            )
            return auth_preflight, headers, None, auth_resolution
        auth_config["captcha"] = captcha_value
    elif captcha_mode == "dynamic":
        captcha_context = await fetch_captcha_context(
            auth_config,
            source=source,
            input_type=input_type,
            target_url=target_url,
            endpoints=endpoints,
        )
        captcha_text = captcha_context.captcha_text if captcha_context.ok else None
        steps.append(
            RunAuthPreflightStep(
                key="captcha",
                label="动态验证码上下文",
                status="passed" if captcha_context.ok else "blocked",
                detail=captcha_context_summary(captcha_context),
            )
        )
        if captcha_context.ok and captcha_text:
            auth_config["captcha"] = captcha_text
        elif captcha_required_by_login(auth_config, login_endpoint):
            auth_preflight = auth_preflight_response(
                auth_mode=auth_mode,
                captcha_mode=captcha_mode,
                test_type=test_type,
                status="blocked",
                strategy="auto_login",
                plan="接口测试只获取验证码上下文，不做图片识别。",
                captcha_handling=captcha_context_summary(captcha_context),
                steps=steps,
                missing_fields=["captcha"],
                next_action="接口动态验证码未返回明文 code；请改用固定验证码并填写验证码。",
            )
            return auth_preflight, headers, None, auth_resolution

    if not login_url:
        steps.append(
            RunAuthPreflightStep(
                key="login_plan",
                label="登录链路",
                status="blocked",
                detail="未提供登录 URL，且无法从 API 文档推断。",
            )
        )
    auth_resolution = await resolve_auto_auth_headers(
        auth_config,
        source=source,
        input_type=input_type,
        target_url=target_url,
        endpoints=endpoints,
    )
    steps.append(
        RunAuthPreflightStep(
            key="login",
            label="登录换取 Token/Cookie",
            status="passed" if auth_resolution.ok else "blocked",
            detail=auth_resolution.detail
            or ("自动获取成功" if auth_resolution.ok else "自动获取失败"),
        )
    )
    if auth_resolution.ok:
        headers.update(auth_resolution.headers)
    else:
        auth_preflight = auth_preflight_response(
            auth_mode=auth_mode,
            captcha_mode=captcha_mode,
            test_type=test_type,
            status="blocked",
            strategy="auto_login",
            plan="根据 OpenAPI 推理 login/token/captcha/csrf 链路，并限制为登录相关接口与只读验证接口。",
            captcha_handling=captcha_context_summary(captcha_context)
            if captcha_mode == "dynamic"
            else {"none": "无验证码。", "static": "固定验证码：使用用户填写的验证码。"}[
                captcha_mode
            ],
            steps=steps,
            missing_fields=auth_resolution.missing_inputs,
            auth_header_name=auth_resolution.header_name,
            next_action=auth_resolution.next_action or "补齐登录信息后重新预检。",
        )
        return auth_preflight, headers, None, auth_resolution

    candidates = protected_candidates
    if not candidates:
        steps.append(
            RunAuthPreflightStep(
                key="protected_validation",
                label="受保护接口验证",
                status="blocked",
                detail="没有可用于验证 Token/Cookie 的受保护只读接口。",
            )
        )
        auth_preflight = auth_preflight_response(
            auth_mode=auth_mode,
            captcha_mode=captcha_mode,
            test_type=test_type,
            status="blocked",
            strategy="auto_login",
            plan="根据 OpenAPI 推理 login/token/captcha/csrf 链路，并限制为登录相关接口与只读验证接口。",
            captcha_handling=captcha_context_summary(captcha_context)
            if captcha_mode == "dynamic"
            else {"none": "无验证码。", "static": "固定验证码：使用用户填写的验证码。"}[
                captcha_mode
            ],
            steps=steps,
            missing_fields=["protected_read_only_endpoint"],
            auth_header_name=auth_resolution.header_name,
            next_action="OpenAPI 需要提供受保护 GET/HEAD/OPTIONS 接口用于鉴权预检。",
        )
        return auth_preflight, headers, None, auth_resolution

    validation_results, success_count = await validate_readonly_auth_access(candidates, headers)
    status = "passed" if success_count >= max(1, min(2, len(candidates))) else "blocked"
    steps.append(
        RunAuthPreflightStep(
            key="protected_validation",
            label="受保护接口验证",
            status=status,
            detail=f"自动鉴权只读验证通过 {success_count}/{len(candidates)} 个接口。",
        )
    )
    runtime_auth_config = auth_config if auth_config.get("enabled") else None
    auth_preflight = auth_preflight_response(
        auth_mode=auth_mode,
        captcha_mode=captcha_mode,
        test_type=test_type,
        status=status,
        strategy="auto_login",
        plan="根据 OpenAPI 推理 login/token/captcha/csrf 链路；执行层仅允许登录、验证码、token/refresh/csrf 相关接口和只读验证接口。",
        captcha_handling=captcha_context_summary(captcha_context)
        if captcha_mode == "dynamic"
        else {"none": "无验证码。", "static": "固定验证码：使用用户填写的验证码。"}[captcha_mode],
        steps=steps,
        validation_results=validation_results,
        auth_header_name=auth_resolution.header_name,
        protected_validation_count=success_count,
        next_action=None
        if status == "passed"
        else "Token/Cookie 获取成功，但受保护接口验证失败；请检查账号权限。",
    )
    return auth_preflight, headers, runtime_auth_config, auth_resolution


async def count_default_vision_models(db: Any) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(LLMProvider)
        .where(
            LLMProvider.is_active.is_(True),
            LLMProvider.is_default_vision.is_(True),
        )
    )
    return int(result.scalar_one())


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

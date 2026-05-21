from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse, urljoin

import httpx

from app.core.redaction import is_sensitive_header


@dataclass
class AuthResolution:
    ok: bool
    headers: dict[str, str] = field(default_factory=dict)
    strategy: str | None = None
    header_name: str | None = None
    detail: str = ""
    missing_inputs: list[str] = field(default_factory=list)
    next_action: str = ""
    required_fields: list[str] = field(default_factory=list)


def coerce_auth_config(config: Any) -> dict[str, Any]:
    if config is None:
        return {}
    if isinstance(config, dict):
        return {str(key): value for key, value in config.items() if value is not None}
    if hasattr(config, "model_dump"):
        return config.model_dump(exclude_none=True)
    if hasattr(config, "dict"):
        return config.dict(exclude_none=True)
    return {}


def normalize_headers(headers: dict[str, Any] | dict | None) -> dict[str, str]:
    if not isinstance(headers, dict):
        return {}
    normalized: dict[str, str] = {}
    for key, value in headers.items():
        key_text = str(key).strip()
        if not key_text or value is None:
            continue
        if isinstance(value, str):
            value_text = value.strip()
        elif isinstance(value, (int, float, bool)):
            value_text = str(value)
        else:
            continue
        if value_text:
            normalized[key_text] = value_text
    return normalized


def has_auth_like_header(headers: dict[str, str] | dict | None) -> bool:
    normalized = normalize_headers(headers)
    return any(is_sensitive_header(name) for name in normalized)


def merge_token_header(token: str | None, headers: dict[str, str]) -> dict[str, str]:
    token_value = (token or "").strip()
    if token_value:
        headers["Authorization"] = (
            token_value if token_value.lower().startswith("bearer ") else f"Bearer {token_value}"
        )
    return headers


def join_auth_url(base_url: str, login_url: str) -> str:
    login_url = login_url.strip()
    if login_url.startswith(("http://", "https://")):
        return login_url
    base = base_url.strip()
    if not base:
        return login_url
    return urljoin(base.rstrip("/") + "/", login_url.lstrip("/"))


def infer_login_url(endpoints: list[dict[str, Any]], base_url: str) -> str | None:
    if not base_url:
        return None
    login_markers = ("login", "signin", "sign-in", "token", "auth")
    candidates: list[dict[str, Any]] = []
    for endpoint in endpoints:
        path = str(endpoint.get("path") or "")
        method = str(endpoint.get("method") or "GET").upper()
        if method not in {"POST", "PUT", "PATCH"}:
            continue
        lowered = path.lower()
        if any(marker in lowered for marker in login_markers):
            candidates.append(endpoint)
    if not candidates:
        return None
    candidates.sort(key=lambda item: 0 if "login" in str(item.get("path", "")).lower() else 1)
    return join_auth_url(base_url, str(candidates[0].get("path") or ""))


def extract_path_value(payload: Any, path: str) -> Any:
    normalized = path.strip()
    if normalized.startswith("$."):
        normalized = normalized[2:]
    elif normalized.startswith("$"):
        normalized = normalized[1:].lstrip(".")
    if not normalized:
        return None

    current = payload
    for part in normalized.split("."):
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if 0 <= index < len(current) else None
        else:
            return None
        if current is None:
            return None
    return current


def find_token_value(payload: Any) -> str | None:
    candidate_paths = (
        "access_token",
        "token",
        "jwt",
        "id_token",
        "data.access_token",
        "data.token",
        "data.jwt",
        "result.access_token",
        "result.token",
        "result.jwt",
        "body.access_token",
        "body.token",
    )
    for path in candidate_paths:
        value = extract_path_value(payload, path)
        if isinstance(value, str) and value.strip():
            return value.strip()

    if isinstance(payload, dict):
        for key, value in payload.items():
            key_text = str(key).lower()
            if key_text in {"access_token", "token", "jwt", "id_token"}:
                if isinstance(value, str) and value.strip():
                    return value.strip()
            nested = find_token_value(value)
            if nested:
                return nested
    elif isinstance(payload, list):
        for item in payload:
            nested = find_token_value(item)
            if nested:
                return nested
    return None


def format_token_header(token: str, prefix: str | None) -> str:
    token = token.strip()
    prefix_text = (prefix or "").strip()
    if not prefix_text:
        return token
    if token.lower().startswith(prefix_text.lower() + " "):
        return token
    return f"{prefix_text} {token}"


async def load_auth_endpoints(source: str, input_type: str) -> tuple[str, list[dict[str, Any]]]:
    if input_type == "url":
        return "", []
    content = source
    if input_type == "swagger_url":
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            response = await client.get(source)
            response.raise_for_status()
            content = response.text
    from app.tools.doc_parser import parse_api_document_content

    return content, parse_api_document_content(content)


def _normalize_name(value: str) -> str:
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())


def _simple_credentials(config: dict[str, Any]) -> dict[str, str]:
    credentials: dict[str, str] = {}
    for name in ("username", "password", "captcha", "tenant"):
        value = config.get(name)
        if isinstance(value, str) and value.strip():
            credentials[name] = value.strip()
    return credentials


def _credential_for_field(field_name: str, credentials: dict[str, str]) -> str | None:
    normalized = _normalize_name(field_name)
    if not normalized:
        return None
    if credentials.get("username") and any(
        marker in normalized
        for marker in ("username", "useraccount", "account", "loginname", "loginid", "mobile", "phone")
    ):
        return credentials["username"]
    if credentials.get("password") and any(marker in normalized for marker in ("password", "passwd", "pwd")):
        return credentials["password"]
    if credentials.get("captcha") and any(
        marker in normalized
        for marker in ("captcha", "verifycode", "verificationcode", "validcode", "validatecode", "code")
    ):
        return credentials["captcha"]
    if credentials.get("tenant") and any(marker in normalized for marker in ("tenant", "tenantid", "tenantcode")):
        return credentials["tenant"]
    return None


def _input_key_for_field(field_name: str) -> str:
    normalized = _normalize_name(field_name)
    if any(
        marker in normalized
        for marker in ("username", "useraccount", "account", "loginname", "loginid", "mobile", "phone")
    ):
        return "username"
    if any(marker in normalized for marker in ("password", "passwd", "pwd")):
        return "password"
    if any(
        marker in normalized
        for marker in ("captcha", "verifycode", "verificationcode", "validcode", "validatecode", "code")
    ):
        return "captcha"
    return "login_body"


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _endpoint_path(endpoint: dict[str, Any]) -> str:
    return str(endpoint.get("path") or "").strip()


def _match_login_endpoint(endpoints: list[dict[str, Any]], login_url: str) -> dict[str, Any] | None:
    parsed_path = urlparse(login_url).path.rstrip("/")
    for endpoint in endpoints:
        endpoint_path = _endpoint_path(endpoint).rstrip("/")
        if endpoint_path and endpoint_path == parsed_path:
            return endpoint
    for endpoint in endpoints:
        endpoint_path = _endpoint_path(endpoint).rstrip("/")
        if endpoint_path and parsed_path.endswith(endpoint_path):
            return endpoint
    return None


def build_login_body(config: dict[str, Any], login_endpoint: dict[str, Any] | None = None) -> dict[str, Any]:
    body = config.get("body")
    if isinstance(body, dict) and body:
        return body

    credentials = _simple_credentials(config)
    if not credentials:
        return body if isinstance(body, dict) else {}

    schema = (login_endpoint or {}).get("request_body_schema")
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if isinstance(properties, dict) and properties:
        mapped: dict[str, Any] = {}
        for field_name in properties:
            value = _credential_for_field(str(field_name), credentials)
            if value is not None:
                mapped[str(field_name)] = value
        if mapped:
            return mapped

    fallback = {
        "username": credentials.get("username"),
        "password": credentials.get("password"),
        "code": credentials.get("captcha"),
        "tenantId": credentials.get("tenant"),
    }
    return {key: value for key, value in fallback.items() if value}


def missing_required_body_fields(
    request_body: dict[str, Any],
    login_endpoint: dict[str, Any] | None = None,
) -> list[str]:
    schema = (login_endpoint or {}).get("request_body_schema")
    required = schema.get("required") if isinstance(schema, dict) else None
    if not isinstance(required, list):
        return []

    missing: list[str] = []
    for field_name in required:
        field_text = str(field_name)
        value = request_body.get(field_text)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(field_text)
    return missing


def missing_inputs_for_body_fields(field_names: list[str]) -> list[str]:
    return _unique([_input_key_for_field(field_name) for field_name in field_names])


async def resolve_auto_auth_headers(
    config: Any,
    *,
    source: str,
    input_type: str,
    target_url: str,
    endpoints: list[dict[str, Any]] | None = None,
) -> AuthResolution:
    config_data = coerce_auth_config(config)
    if not config_data or not config_data.get("enabled"):
        return AuthResolution(ok=False, detail="未启用自动获取 Token")

    method = str(config_data.get("method") or "POST").upper()
    if method not in {"POST", "PUT", "PATCH"}:
        return AuthResolution(
            ok=False,
            detail="自动获取 Token 仅支持 POST/PUT/PATCH",
            missing_inputs=["method"],
            next_action="在高级登录选项中选择 POST、PUT 或 PATCH。",
        )

    loaded_endpoints = endpoints
    login_url = str(config_data.get("login_url") or "").strip()
    if not login_url:
        try:
            if loaded_endpoints is None:
                _, loaded_endpoints = await load_auth_endpoints(source, input_type)
            login_url = infer_login_url(loaded_endpoints or [], target_url) or ""
        except Exception:
            login_url = ""
    else:
        login_url = join_auth_url(target_url, login_url)
    if not login_url:
        return AuthResolution(
            ok=False,
            detail="未提供登录 URL，且无法从 API 文档推断",
            missing_inputs=["login_url"],
            next_action="在高级登录选项中填写登录 URL，或确认 API 文档包含 login/token 接口。",
        )
    if not login_url.startswith(("http://", "https://")):
        return AuthResolution(
            ok=False,
            detail="登录 URL 必须是 http(s) 地址，或提供 Base URL 后使用相对路径",
            missing_inputs=["base_url", "login_url"],
            next_action="填写 Base URL，或把登录 URL 改成完整 http(s) 地址。",
        )

    if loaded_endpoints is None and input_type != "url":
        try:
            _, loaded_endpoints = await load_auth_endpoints(source, input_type)
        except Exception:
            loaded_endpoints = []

    login_endpoint = _match_login_endpoint(loaded_endpoints or [], login_url)
    content_type = str(config_data.get("content_type") or "").lower()
    if not content_type and login_endpoint:
        endpoint_content_type = login_endpoint.get("request_body_content_type")
        content_type = "form" if "form" in str(endpoint_content_type or "").lower() else "json"
    content_type = content_type or "json"
    request_headers = normalize_headers(config_data.get("headers"))
    request_body = build_login_body(config_data, login_endpoint)
    missing_fields = missing_required_body_fields(request_body, login_endpoint)
    if missing_fields:
        missing_inputs = missing_inputs_for_body_fields(missing_fields)
        next_action = (
            "补充标出的基础登录凭据后重新运行预检。"
            if "login_body" not in missing_inputs
            else "补充基础登录凭据；无法自动映射的字段请在高级登录选项中填写登录请求体 JSON。"
        )
        return AuthResolution(
            ok=False,
            detail=f"登录请求体缺少必填字段：{', '.join(missing_fields)}",
            missing_inputs=missing_inputs,
            next_action=next_action,
            required_fields=missing_fields,
        )
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            if content_type == "form":
                response = await client.request(
                    method,
                    login_url,
                    headers=request_headers or None,
                    data=request_body,
                )
            else:
                request_headers.setdefault("Content-Type", "application/json")
                response = await client.request(
                    method,
                    login_url,
                    headers=request_headers or None,
                    json=request_body,
                )
    except httpx.TimeoutException:
        return AuthResolution(
            ok=False,
            detail="登录请求超时",
            missing_inputs=["login_url"],
            next_action="检查登录 URL、Base URL 和网络可达性。",
        )
    except httpx.RequestError:
        return AuthResolution(
            ok=False,
            detail="登录请求失败，请检查登录 URL、网络和请求体",
            missing_inputs=["login_url", "login_body"],
            next_action="检查登录 URL 是否正确；如果登录接口需要特殊字段，请在高级登录选项中填写请求体 JSON。",
        )

    if response.status_code >= 400:
        if response.status_code in {400, 422}:
            missing_inputs = ["login_body"]
            next_action = "登录接口拒绝了请求体，请在高级登录选项中补充或调整登录请求体 JSON。"
        elif response.status_code in {401, 403}:
            missing_inputs = ["username", "password", "captcha", "login_headers"]
            next_action = "检查账号、密码、验证码；如果接口还需要额外 Header，请在高级登录选项中补充。"
        else:
            missing_inputs = ["login_url", "login_body"]
            next_action = "检查登录 URL、请求体和目标环境状态。"
        return AuthResolution(
            ok=False,
            detail=f"登录接口返回 HTTP {response.status_code}",
            missing_inputs=missing_inputs,
            next_action=next_action,
        )

    try:
        response_payload = response.json()
    except ValueError:
        response_payload = {}

    token: str | None = None
    token_path = str(config_data.get("token_path") or "").strip()
    if token_path:
        value = extract_path_value(response_payload, token_path)
        if isinstance(value, str) and value.strip():
            token = value.strip()
        elif isinstance(value, (int, float)):
            token = str(value)
    else:
        token = find_token_value(response_payload)

    header_name = str(config_data.get("header_name") or "Authorization").strip() or "Authorization"
    if not token and header_name.lower() == "cookie":
        cookie_value = response.headers.get("set-cookie")
        if cookie_value:
            token = cookie_value

    if not token:
        detail = "登录成功，但响应中没有找到 Token"
        if token_path:
            detail = f"登录成功，但响应中没有找到 token_path={token_path}"
        return AuthResolution(
            ok=False,
            detail=detail,
            missing_inputs=["token_path"],
            next_action="在高级登录选项中填写响应里的 Token 路径，例如 data.token、access_token 或 result.token。",
        )

    token_prefix_value = config_data["token_prefix"] if "token_prefix" in config_data else "Bearer"
    token_prefix = "" if token_prefix_value is None else str(token_prefix_value)
    header_value = token if header_name.lower() == "cookie" else format_token_header(token, token_prefix)
    return AuthResolution(
        ok=True,
        headers={header_name: header_value},
        strategy="auto_login",
        header_name=header_name,
        detail="自动获取 Token 成功，已获取鉴权头",
    )

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse, urljoin

import httpx

from app.core.redaction import is_sensitive_header, redact_sensitive_text


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


@dataclass
class CaptchaContextResolution:
    ok: bool
    endpoint: str | None = None
    method: str = "GET"
    context: dict[str, Any] = field(default_factory=dict)
    captcha_text: str | None = None
    detail: str = ""
    missing_inputs: list[str] = field(default_factory=list)
    next_action: str = ""


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


def _normalize_name(value: str) -> str:
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())


_LOGIN_METHODS = {"POST", "PUT", "PATCH"}
_LOGIN_MARKERS = ("login", "signin", "token", "auth", "session", "csrf", "xsrf")
_SIMPLE_LOGIN_PATHS = {"/login", "/auth/login", "/user/login", "/system/login"}
_SIMPLE_LOGIN_SEGMENTS = {"login", "signin", "session"}
_SPECIALIZED_LOGIN_MARKERS = (
    "xcx",
    "sms",
    "email",
    "wechat",
    "weixin",
    "oauth",
    "sso",
    "refresh",
    "logout",
    "register",
    "captcha",
)
_NON_LOGIN_MARKERS = {"refresh", "logout", "register", "captcha"}
_SPECIALIZED_CODE_MARKERS = {"xcx", "sms", "email", "wechat", "weixin", "oauth", "sso"}
_JSON_SCHEMA_KEYWORDS = {
    "type",
    "required",
    "properties",
    "items",
    "allof",
    "anyof",
    "oneof",
    "description",
    "title",
    "format",
    "default",
    "example",
    "enum",
    "nullable",
    "additionalproperties",
}


def _endpoint_text(endpoint: dict[str, Any]) -> str:
    tags = endpoint.get("tags")
    tag_text = " ".join(str(tag) for tag in tags) if isinstance(tags, list) else str(tags or "")
    return " ".join(
        str(value or "")
        for value in (
            endpoint.get("path"),
            endpoint.get("summary"),
            endpoint.get("operationId"),
            endpoint.get("description"),
            tag_text,
        )
    )


def _schema_field_names(schema: Any) -> list[str]:
    if not isinstance(schema, dict):
        return []
    fields: list[str] = []
    properties = schema.get("properties")
    if isinstance(properties, dict):
        fields.extend(str(name) for name in properties)
    else:
        for name in schema:
            normalized = _normalize_name(str(name))
            if normalized not in _JSON_SCHEMA_KEYWORDS:
                fields.append(str(name))
    required = schema.get("required")
    if isinstance(required, list):
        fields.extend(str(name) for name in required)
    return fields


def _endpoint_field_names(endpoint: dict[str, Any]) -> list[str]:
    fields = _schema_field_names(endpoint.get("request_body_schema"))
    required_fields = endpoint.get("required_fields")
    if isinstance(required_fields, list):
        fields.extend(str(name) for name in required_fields)
    for group in ("path_params", "query_params", "header_params"):
        params = endpoint.get(group)
        if not isinstance(params, list):
            continue
        for param in params:
            if isinstance(param, dict) and param.get("name"):
                fields.append(str(param["name"]))

    seen: set[str] = set()
    unique_fields: list[str] = []
    for field_name in fields:
        normalized = _normalize_name(field_name)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique_fields.append(field_name)
    return unique_fields


def _endpoint_required_field_names(endpoint: dict[str, Any]) -> list[str]:
    required: list[str] = []
    schema = endpoint.get("request_body_schema")
    if isinstance(schema, dict) and isinstance(schema.get("required"), list):
        required.extend(str(name) for name in schema["required"])
    required_fields = endpoint.get("required_fields")
    if isinstance(required_fields, list):
        required.extend(str(name) for name in required_fields)
    for group in ("path_params", "query_params", "header_params"):
        params = endpoint.get(group)
        if not isinstance(params, list):
            continue
        for param in params:
            if isinstance(param, dict) and param.get("required") and param.get("name"):
                required.append(str(param["name"]))

    seen: set[str] = set()
    unique_required: list[str] = []
    for field_name in required:
        normalized = _normalize_name(field_name)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique_required.append(field_name)
    return unique_required


def _login_field_kind(field_name: str) -> str | None:
    normalized = _normalize_name(field_name)
    if any(
        marker in normalized
        for marker in ("username", "useraccount", "account", "loginname", "loginid")
    ):
        return "username"
    if any(marker in normalized for marker in ("mobile", "phone")):
        return "username"
    if any(marker in normalized for marker in ("password", "passwd", "pwd")):
        return "password"
    if any(marker in normalized for marker in ("tenant", "tenantid", "tenantcode")):
        return "tenant"
    if any(
        marker in normalized
        for marker in ("captcha", "verifycode", "verificationcode", "validcode", "validatecode")
    ):
        return "captcha"
    if normalized == "code":
        return "captcha"
    if any(marker in normalized for marker in ("csrf", "csrftoken", "xsrf", "xsrftoken")):
        return "csrf"
    return None


def _specialized_code_field(field_name: str) -> bool:
    normalized = _normalize_name(field_name)
    return "code" in normalized and any(
        marker in normalized for marker in _SPECIALIZED_CODE_MARKERS
    )


def _body_supplies_field(body: Any, field_name: str) -> bool:
    if not isinstance(body, dict):
        return False
    normalized_field = _normalize_name(field_name)
    for key, value in body.items():
        if _normalize_name(str(key)) != normalized_field:
            continue
        return value is not None and not (isinstance(value, str) and not value.strip())
    return False


def _config_supplies_field(
    field_name: str,
    *,
    config_data: dict[str, Any],
    credentials: dict[str, str],
) -> bool:
    body = config_data.get("body")
    if _body_supplies_field(body, field_name):
        return True
    if _specialized_code_field(field_name):
        return False
    kind = _login_field_kind(field_name)
    if kind and credentials.get(kind):
        return True
    return False


def _login_endpoint_score(
    endpoint: dict[str, Any], config_data: dict[str, Any]
) -> tuple[int, int, int]:
    path = str(endpoint.get("path") or "").strip()
    path_lower = path.lower().rstrip("/") or path.lower()
    normalized_path = _normalize_name(path)
    segments = [segment for segment in path_lower.split("/") if segment]
    last_segment = _normalize_name(segments[-1]) if segments else ""
    text = _endpoint_text(endpoint)
    normalized_text = _normalize_name(text)
    field_names = _endpoint_field_names(endpoint)
    required_fields = _endpoint_required_field_names(endpoint)
    credentials = _simple_credentials(config_data)

    score = 0
    if path_lower in _SIMPLE_LOGIN_PATHS:
        score += 45
    if last_segment in _SIMPLE_LOGIN_SEGMENTS:
        score += 80
    elif last_segment in {"token", "session"}:
        score += 30
    if "login" in normalized_path:
        score += 35
    if "signin" in normalized_path:
        score += 25
    if "token" in normalized_path:
        score += 10
    if "auth" in normalized_path:
        score += 8
    if "session" in normalized_path:
        score += 8

    has_username = any(_login_field_kind(field) == "username" for field in field_names)
    has_password = any(_login_field_kind(field) == "password" for field in field_names)
    if has_username and has_password:
        score += 90
    elif has_password:
        score += 30
    elif has_username:
        score += 20

    missing_required = [
        field
        for field in required_fields
        if not _config_supplies_field(field, config_data=config_data, credentials=credentials)
    ]
    body_supplied_count = sum(
        1 for field in required_fields if _body_supplies_field(config_data.get("body"), field)
    )
    if required_fields and body_supplied_count == len(required_fields):
        score += 120
    else:
        score += 35 * body_supplied_count
    score += 30 * (len(required_fields) - len(missing_required))
    score -= 45 * len(missing_required)
    score -= 70 * sum(1 for field in missing_required if _specialized_code_field(field))

    specialized_hits = [
        marker
        for marker in _SPECIALIZED_LOGIN_MARKERS
        if marker in normalized_text or marker in normalized_path
    ]
    if specialized_hits:
        schema_matches = bool(required_fields) and not missing_required
        score -= (10 if schema_matches else 55) * len(set(specialized_hits))

    for marker in _NON_LOGIN_MARKERS:
        if marker in normalized_text or marker in normalized_path:
            score -= 120
    if last_segment in _NON_LOGIN_MARKERS:
        score -= 80

    return score, len(missing_required), len(set(specialized_hits))


def infer_login_url(
    endpoints: list[dict[str, Any]],
    base_url: str,
    config: Any = None,
) -> str | None:
    if not base_url:
        return None
    config_data = coerce_auth_config(config)
    candidates: list[tuple[int, int, int, int, int, dict[str, Any]]] = []
    for index, endpoint in enumerate(endpoints):
        path = str(endpoint.get("path") or "")
        method = str(endpoint.get("method") or "GET").upper()
        if method not in _LOGIN_METHODS:
            continue
        normalized_text = _normalize_name(_endpoint_text(endpoint))
        if any(marker in normalized_text for marker in _LOGIN_MARKERS):
            score, missing_count, specialized_count = _login_endpoint_score(endpoint, config_data)
            candidates.append((score, missing_count, specialized_count, len(path), index, endpoint))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1], item[2], item[3], item[4]))
    return join_auth_url(base_url, str(candidates[0][5].get("path") or ""))


def infer_captcha_url(endpoints: list[dict[str, Any]], base_url: str) -> str | None:
    if not base_url:
        return None
    markers = ("captcha", "verifycode", "verificationcode", "validcode", "validatecode")
    candidates: list[dict[str, Any]] = []
    for endpoint in endpoints:
        path = str(endpoint.get("path") or "")
        method = str(endpoint.get("method") or "GET").upper()
        if method not in {"GET", "HEAD", "OPTIONS"}:
            continue
        lowered = path.lower().replace("_", "").replace("-", "")
        if any(marker in lowered for marker in markers):
            candidates.append(endpoint)
    if not candidates:
        return None
    candidates.sort(key=lambda item: str(item.get("path") or "").lower().find("captcha"))
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


def _looks_token_like(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    lowered = text.lower()
    if lowered.startswith(("bearer ", "basic ")):
        return len(text.split(maxsplit=1)) == 2 and len(text.split(maxsplit=1)[1]) >= 8
    if text.count(".") >= 2 and len(text) >= 20:
        return True
    if len(text) < 10:
        return False
    has_alnum = any(ch.isalnum() for ch in text)
    has_token_marker = any(ch in text for ch in "._-=+/") or any(ch.isdigit() for ch in text)
    return has_alnum and (has_token_marker or len(text) >= 24)


def _coerce_token_value(value: Any, *, key_hint: str = "") -> str | None:
    if not isinstance(value, str):
        return None
    token = value.strip()
    if not token:
        return None
    if key_hint == "data" and not _looks_token_like(token):
        return None
    return token


def find_token_value(payload: Any) -> str | None:
    candidate_paths = (
        "access_token",
        "accessToken",
        "token",
        "jwt",
        "id_token",
        "idToken",
        "authorization",
        "Authorization",
        "session",
        "sessionId",
        "session_id",
        "authToken",
        "bearerToken",
        "data.access_token",
        "data.accessToken",
        "data.token",
        "data.jwt",
        "data.id_token",
        "data.idToken",
        "data.authorization",
        "data.Authorization",
        "data.session",
        "data.sessionId",
        "data.session_id",
        "data.authToken",
        "data.bearerToken",
        "result.access_token",
        "result.accessToken",
        "result.token",
        "result.jwt",
        "result.authorization",
        "result.Authorization",
        "result.session",
        "result.sessionId",
        "result.session_id",
        "body.access_token",
        "body.accessToken",
        "body.token",
        "body.authorization",
        "body.Authorization",
        "body.session",
        "body.sessionId",
        "body.session_id",
    )
    for path in candidate_paths:
        value = extract_path_value(payload, path)
        token = _coerce_token_value(value)
        if token:
            return token

    if isinstance(payload, dict):
        for key, value in payload.items():
            key_text = _normalize_name(str(key))
            if key_text in {
                "accesstoken",
                "token",
                "jwt",
                "idtoken",
                "authorization",
                "session",
                "sessionid",
                "authtoken",
                "bearertoken",
            }:
                token = _coerce_token_value(value)
                if token:
                    return token
            if key_text == "data":
                token = _coerce_token_value(value, key_hint="data")
                if token:
                    return token
            nested = find_token_value(value)
            if nested:
                return nested
    elif isinstance(payload, list):
        for item in payload:
            nested = find_token_value(item)
            if nested:
                return nested
    return None


def find_cookie_value(payload: Any) -> str | None:
    candidate_paths = (
        "cookie",
        "Cookie",
        "set_cookie",
        "setCookie",
        "session_cookie",
        "sessionCookie",
        "data.cookie",
        "data.Cookie",
        "data.set_cookie",
        "data.setCookie",
        "data.session_cookie",
        "data.sessionCookie",
        "result.cookie",
        "result.Cookie",
        "result.set_cookie",
        "result.setCookie",
        "body.cookie",
        "body.Cookie",
        "body.set_cookie",
        "body.setCookie",
    )
    for path in candidate_paths:
        value = extract_path_value(payload, path)
        if isinstance(value, str) and value.strip():
            return value.strip()

    if isinstance(payload, dict):
        for key, value in payload.items():
            normalized = _normalize_name(str(key))
            if normalized in {"cookie", "setcookie", "sessioncookie"}:
                if isinstance(value, str) and value.strip():
                    return value.strip()
            nested = find_cookie_value(value)
            if nested:
                return nested
    elif isinstance(payload, list):
        for item in payload:
            nested = find_cookie_value(item)
            if nested:
                return nested
    return None


def _envelope_success_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value) in {0, 200}
    if isinstance(value, str):
        normalized = value.strip().lower()
        return normalized in {"0", "00", "000", "200", "ok", "success", "succeeded", "true"}
    return False


def _coerce_status_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _login_failure_message(payload: dict[str, Any]) -> str:
    for key in ("msg", "message", "error_description", "error", "detail"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return redact_sensitive_text(value.strip())[:180]
    return ""


def _login_failure_guidance(status_value: Any, message: str) -> tuple[list[str], str]:
    status_code = _coerce_status_int(status_value)
    if status_code in {401, 403}:
        return (
            ["username", "password", "captcha", "login_headers"],
            "检查账号、密码、验证码；如果接口还需要额外 Header，请补充登录请求头。",
        )

    normalized_message = _normalize_name(message)
    if any(
        marker in normalized_message
        for marker in (
            "username",
            "useraccount",
            "account",
            "password",
            "passwd",
            "pwd",
            "captcha",
            "verifycode",
            "verificationcode",
            "validcode",
            "validatecode",
            "credential",
            "loginfailed",
        )
    ):
        return (
            ["username", "password", "captcha"],
            "检查账号、密码、验证码后重新运行预检。",
        )

    return (
        ["username", "password", "captcha", "login_body"],
        "检查账号、密码、验证码；如果接口还需要特殊字段，请调整登录请求体 JSON。",
    )


def _login_envelope_failure(payload: Any) -> AuthResolution | None:
    if not isinstance(payload, dict):
        return None

    for key in ("code", "status", "status_code"):
        if key not in payload:
            continue
        value = payload.get(key)
        if value is None or _envelope_success_value(value):
            continue

        message = _login_failure_message(payload)
        missing_inputs, next_action = _login_failure_guidance(value, message)
        value_text = redact_sensitive_text(str(value))[:40]
        detail = f"登录接口返回业务失败：{key}={value_text}"
        if message:
            detail = f"{detail}，{message}"
        return AuthResolution(
            ok=False,
            detail=detail,
            missing_inputs=missing_inputs,
            next_action=next_action,
        )
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


def _simple_credentials(config: dict[str, Any]) -> dict[str, str]:
    credentials: dict[str, str] = {}
    for name in ("username", "password", "captcha", "tenant", "csrf"):
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
        for marker in (
            "username",
            "useraccount",
            "account",
            "loginname",
            "loginid",
            "mobile",
            "phone",
        )
    ):
        return credentials["username"]
    if credentials.get("password") and any(
        marker in normalized for marker in ("password", "passwd", "pwd")
    ):
        return credentials["password"]
    if credentials.get("captcha") and any(
        marker in normalized
        for marker in (
            "captcha",
            "verifycode",
            "verificationcode",
            "validcode",
            "validatecode",
            "code",
        )
    ):
        return credentials["captcha"]
    if credentials.get("tenant") and any(
        marker in normalized for marker in ("tenant", "tenantid", "tenantcode")
    ):
        return credentials["tenant"]
    if credentials.get("csrf") and any(
        marker in normalized for marker in ("csrf", "csrftoken", "xsrf", "xsrftoken")
    ):
        return credentials["csrf"]
    return None


_CREDENTIAL_PLACEHOLDER_RE = re.compile(
    r"\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}|\$\{\s*([A-Za-z0-9_.-]+)\s*\}"
)


def _credential_for_placeholder(name: str, credentials: dict[str, str]) -> str | None:
    normalized = _normalize_name(name)
    direct_aliases = {
        "user": "username",
        "name": "username",
        "username": "username",
        "account": "username",
        "password": "password",
        "passwd": "password",
        "pwd": "password",
        "captcha": "captcha",
        "code": "captcha",
        "tenant": "tenant",
        "tenantid": "tenant",
        "csrf": "csrf",
        "csrftoken": "csrf",
        "xsrf": "csrf",
        "xsrftoken": "csrf",
    }
    direct_key = direct_aliases.get(normalized)
    if direct_key and credentials.get(direct_key):
        return credentials[direct_key]
    return _credential_for_field(name, credentials)


def _replace_credential_placeholders(value: Any, credentials: dict[str, str]) -> Any:
    if not credentials:
        return value
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            placeholder = match.group(1) or match.group(2) or ""
            return _credential_for_placeholder(placeholder, credentials) or match.group(0)

        return _CREDENTIAL_PLACEHOLDER_RE.sub(replace, value)
    if isinstance(value, list):
        return [_replace_credential_placeholders(item, credentials) for item in value]
    if isinstance(value, dict):
        return {
            key: _replace_credential_placeholders(item, credentials)
            for key, item in value.items()
        }
    return value


def _input_key_for_field(field_name: str) -> str:
    normalized = _normalize_name(field_name)
    if any(
        marker in normalized
        for marker in (
            "username",
            "useraccount",
            "account",
            "loginname",
            "loginid",
            "mobile",
            "phone",
        )
    ):
        return "username"
    if any(marker in normalized for marker in ("password", "passwd", "pwd")):
        return "password"
    if any(
        marker in normalized
        for marker in (
            "captcha",
            "verifycode",
            "verificationcode",
            "validcode",
            "validatecode",
            "code",
        )
    ):
        return "captcha"
    if any(marker in normalized for marker in ("csrf", "csrftoken", "xsrf", "xsrftoken")):
        return "csrf"
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


def build_login_body(
    config: dict[str, Any], login_endpoint: dict[str, Any] | None = None
) -> dict[str, Any]:
    body = config.get("body")
    credentials = _simple_credentials(config)
    if isinstance(body, dict) and body:
        return _replace_credential_placeholders(body, credentials)

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


def login_endpoint_for_config(
    config: Any,
    *,
    endpoints: list[dict[str, Any]],
    target_url: str,
) -> tuple[str | None, dict[str, Any] | None]:
    config_data = coerce_auth_config(config)
    login_url = str(config_data.get("login_url") or "").strip()
    if login_url:
        login_url = join_auth_url(target_url, login_url)
    else:
        login_url = infer_login_url(endpoints, target_url, config=config_data) or ""
    if not login_url:
        return None, None
    return login_url, _match_login_endpoint(endpoints, login_url)


def captcha_required_by_login(config: Any, login_endpoint: dict[str, Any] | None = None) -> bool:
    body = build_login_body(coerce_auth_config(config), login_endpoint)
    required_fields = missing_required_body_fields(body, login_endpoint)
    if any(_input_key_for_field(field) == "captcha" for field in required_fields):
        return True
    return any(_input_key_for_field(field) == "captcha" for field in body)


def _extract_context_fields(payload: Any) -> dict[str, Any]:
    context: dict[str, Any] = {}
    interesting = {
        "uuid",
        "id",
        "captchaid",
        "captchakey",
        "key",
        "sessionid",
        "session",
        "csrf",
        "csrftoken",
        "xsrf",
        "xsrftoken",
        "img",
        "image",
        "imagebase64",
        "captchaenabled",
        "enabled",
    }

    def visit(value: Any, prefix: str = "") -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                key_text = str(key)
                normalized = _normalize_name(key_text)
                next_prefix = f"{prefix}.{key_text}" if prefix else key_text
                if normalized in interesting:
                    if isinstance(nested, (str, int, float, bool)) or nested is None:
                        context[next_prefix] = nested
                    elif isinstance(nested, (dict, list)):
                        context[next_prefix] = f"{type(nested).__name__}:{len(nested)}"
                visit(nested, next_prefix)
        elif isinstance(value, list):
            for index, nested in enumerate(value[:5]):
                visit(nested, f"{prefix}[{index}]")

    visit(payload)
    return context


def _extract_clear_captcha_text(payload: Any) -> str | None:
    exact_names = {
        "captcha",
        "captchacode",
        "verifycode",
        "verificationcode",
        "validcode",
        "validatecode",
        "text",
    }

    def visit(value: Any, key_hint: str = "") -> str | None:
        if isinstance(value, dict):
            for key, nested in value.items():
                normalized = _normalize_name(str(key))
                if normalized in exact_names and isinstance(nested, (str, int, float)):
                    text = str(nested).strip()
                    if 2 <= len(text) <= 12:
                        return text
                found = visit(nested, str(key))
                if found:
                    return found
        elif isinstance(value, list):
            for nested in value:
                found = visit(nested, key_hint)
                if found:
                    return found
        elif _normalize_name(key_hint) == "code" and isinstance(value, str):
            text = value.strip()
            if 2 <= len(text) <= 8 and text not in {"0", "00", "200"}:
                return text
        return None

    return visit(payload)


async def fetch_captcha_context(
    config: Any,
    *,
    source: str,
    input_type: str,
    target_url: str,
    endpoints: list[dict[str, Any]] | None = None,
) -> CaptchaContextResolution:
    config_data = coerce_auth_config(config)
    loaded_endpoints = endpoints
    captcha_url = str(config_data.get("captcha_url") or "").strip()
    if captcha_url:
        captcha_url = join_auth_url(target_url, captcha_url)
    else:
        try:
            if loaded_endpoints is None:
                _, loaded_endpoints = await load_auth_endpoints(source, input_type)
            captcha_url = infer_captcha_url(loaded_endpoints or [], target_url) or ""
        except Exception:
            captcha_url = ""

    if not captcha_url:
        return CaptchaContextResolution(
            ok=False,
            detail="未提供验证码 URL，且无法从 API 文档推断",
            missing_inputs=["captcha_url"],
            next_action="填写固定验证码，或在补充登录字段中填写验证码接口 URL。",
        )
    if not captcha_url.startswith(("http://", "https://")):
        return CaptchaContextResolution(
            ok=False,
            endpoint=captcha_url,
            detail="验证码 URL 必须是 http(s) 地址，或提供 Base URL 后使用相对路径",
            missing_inputs=["base_url", "captcha_url"],
            next_action="填写 Base URL，或把验证码 URL 改成完整 http(s) 地址。",
        )

    credentials = _simple_credentials(config_data)
    request_headers = normalize_headers(
        _replace_credential_placeholders(config_data.get("headers"), credentials)
    )
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            response = await client.get(captcha_url, headers=request_headers or None)
    except httpx.TimeoutException:
        return CaptchaContextResolution(
            ok=False,
            endpoint=captcha_url,
            detail="验证码请求超时",
            missing_inputs=["captcha_url"],
            next_action="检查验证码 URL、Base URL 和网络可达性。",
        )
    except httpx.RequestError:
        return CaptchaContextResolution(
            ok=False,
            endpoint=captcha_url,
            detail="验证码请求失败",
            missing_inputs=["captcha_url"],
            next_action="检查验证码 URL 是否正确；接口测试不会识别验证码图片。",
        )

    context: dict[str, Any] = {
        "status_code": response.status_code,
        "cookie_names": list(response.cookies.keys()),
    }
    for header_name in ("x-csrf-token", "x-xsrf-token", "csrf-token", "set-cookie"):
        header_value = response.headers.get(header_name)
        if header_value:
            context.setdefault("header_names", []).append(header_name)

    payload: Any
    try:
        payload = response.json()
    except ValueError:
        payload = {}
        text = str(getattr(response, "text", "") or "")
        if text.strip():
            context["body_preview_type"] = "text"
            context["body_chars"] = len(text)

    if isinstance(payload, (dict, list)):
        context.update(_extract_context_fields(payload))
    captcha_text = _extract_clear_captcha_text(payload)
    if response.status_code >= 400:
        return CaptchaContextResolution(
            ok=False,
            endpoint=captcha_url,
            context=context,
            detail=f"验证码接口返回 HTTP {response.status_code}",
            missing_inputs=["captcha_url"],
            next_action="检查验证码 URL 和环境状态。",
        )

    return CaptchaContextResolution(
        ok=True,
        endpoint=captcha_url,
        context=context,
        captcha_text=captcha_text,
        detail="已获取验证码上下文字段"
        if not captcha_text
        else "已获取验证码上下文字段和明文验证码",
    )


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
            next_action="在补充登录字段中选择 POST、PUT 或 PATCH。",
        )

    loaded_endpoints = endpoints
    login_url = str(config_data.get("login_url") or "").strip()
    if not login_url:
        try:
            if loaded_endpoints is None:
                _, loaded_endpoints = await load_auth_endpoints(source, input_type)
            login_url = (
                infer_login_url(
                    loaded_endpoints or [],
                    target_url,
                    config=config_data,
                )
                or ""
            )
        except Exception:
            login_url = ""
    else:
        login_url = join_auth_url(target_url, login_url)
    if not login_url:
        return AuthResolution(
            ok=False,
            detail="未提供登录 URL，且无法从 API 文档推断",
            missing_inputs=["login_url"],
            next_action="在补充登录字段中填写登录 URL，或确认 API 文档包含 login/token 接口。",
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
    credentials = _simple_credentials(config_data)
    request_headers = normalize_headers(
        _replace_credential_placeholders(config_data.get("headers"), credentials)
    )
    request_body = build_login_body(config_data, login_endpoint)
    missing_fields = missing_required_body_fields(request_body, login_endpoint)
    if missing_fields:
        missing_inputs = missing_inputs_for_body_fields(missing_fields)
        next_action = (
            "补充标出的基础登录凭据后重新运行预检。"
            if "login_body" not in missing_inputs
            else "补充基础登录凭据；无法自动映射的字段请在补充登录字段中填写登录请求体 JSON。"
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
            next_action="检查登录 URL 是否正确；如果登录接口需要特殊字段，请在补充登录字段中填写请求体 JSON。",
        )

    if response.status_code >= 400:
        if response.status_code in {400, 422}:
            missing_inputs = ["login_body"]
            next_action = "登录接口拒绝了请求体，请在补充登录字段中补充或调整登录请求体 JSON。"
        elif response.status_code in {401, 403}:
            missing_inputs = ["username", "password", "captcha", "login_headers"]
            next_action = (
                "检查账号、密码、验证码；如果接口还需要额外 Header，请在补充登录字段中补充。"
            )
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

    envelope_failure = _login_envelope_failure(response_payload)
    if envelope_failure is not None:
        return envelope_failure

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
    cookie_value = response.headers.get("set-cookie") or find_cookie_value(response_payload)
    if not cookie_value:
        try:
            cookie_value = "; ".join(
                f"{name}={value}" for name, value in response.cookies.items()
            )
        except Exception:
            cookie_value = ""
    if not token and header_name.lower() == "cookie" and cookie_value:
        token = cookie_value
    elif not token and cookie_value:
        token = cookie_value
        header_name = "Cookie"

    if not token:
        detail = "登录成功，但响应中没有找到 Token"
        if token_path:
            detail = f"登录成功，但响应中没有找到 token_path={token_path}"
        return AuthResolution(
            ok=False,
            detail=detail,
            missing_inputs=["token_path"],
            next_action="在补充登录字段中填写响应里的 Token 路径，例如 data.token、access_token 或 result.token。",
        )

    token_prefix_value = config_data["token_prefix"] if "token_prefix" in config_data else "Bearer"
    token_prefix = "" if token_prefix_value is None else str(token_prefix_value)
    header_value = (
        token if header_name.lower() == "cookie" else format_token_header(token, token_prefix)
    )
    return AuthResolution(
        ok=True,
        headers={header_name: header_value},
        strategy="auto_login",
        header_name=header_name,
        detail="自动获取 Token 成功，已获取鉴权头",
    )

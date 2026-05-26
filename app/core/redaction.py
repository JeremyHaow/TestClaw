import json
import re
from typing import Any


REDACTED_VALUE = "[REDACTED]"

_SENSITIVE_HEADER_NAMES = {
    "authorization",
    "proxy-authorization",
    "auth",
    "authentication",
    "x-auth",
    "x-api-key",
    "api-key",
    "session",
    "session-id",
    "sessionid",
    "sid",
    "jwt",
    "csrf",
    "csrf-token",
    "xsrf",
    "xsrf-token",
    "captcha",
    "mfa",
    "otp",
}
_HEADER_CONTAINER_KEYS = {
    "headers",
    "request_headers",
    "auth_headers",
    "custom_headers",
}
_SAFE_SENSITIVE_METADATA_KEYS = {
    "allow_out_of_schema_api_cases",
    "api_execution_policy",
    "auth_summary",
    "auth_mode",
    "auth_required",
    "auth_refreshed",
    "auth_resolved",
    "authentication_required",
    "captcha_mode",
}
_SENSITIVE_TEXT_KEY = (
    r"(?:password|passwd|access[_-]?token|refresh[_-]?token|id[_-]?token|"
    r"token|secret|api[_-]?key|authorization|authentication|auth|cookie|credential|"
    r"session[_-]?id|session|sid|jwt|csrf|xsrf|captcha|mfa|otp)"
)
_QUOTED_SECRET_RE = re.compile(
    rf"(?i)([\"']?\b{_SENSITIVE_TEXT_KEY}\b[\"']?\s*[:=]\s*)([\"'])(.*?)(\2)"
)
_BARE_SECRET_RE = re.compile(
    rf"(?i)([\"']?\b{_SENSITIVE_TEXT_KEY}\b[\"']?\s*[:=]\s*)([^\s,;}}]+)"
)
_SPACED_SECRET_RE = re.compile(
    rf"(?i)(\b{_SENSITIVE_TEXT_KEY}\b\s+)"
    r"(?=[^\s,;)}\]]*(?:[0-9._~+/=-]|secret|token|key))([^\s,;)}\]]+)"
)
_AUTH_SCHEME_RE = re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+")
_SENSITIVE_HEADER_SUBSTRINGS = (
    "auth",
    "apikey",
    "token",
    "cookie",
    "password",
    "passwd",
    "secret",
    "credential",
    "captcha",
    "mfa",
    "otp",
    "session",
    "jwt",
    "csrf",
    "xsrf",
    "sid",
)
_PLAYWRIGHT_TWO_QUOTED_ARGS_RE = re.compile(
    r"(?i)\b(fill|type)\s+([\"'])(.*?)(\2)\s+([\"'])(.*?)(\5)"
)
_PLAYWRIGHT_UNQUOTED_SELECTOR_QUOTED_VALUE_RE = re.compile(
    r"(?i)\b(fill|type)\s+([^\s\"']+)\s+([\"'])(.*?)(\3)"
)
_PLAYWRIGHT_UNQUOTED_VALUE_RE = re.compile(
    r"(?i)\b(fill|type)\s+([^\s\"']+)\s+([^\s\"'\[,;)}\]]+)"
)
_UNSAFE_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _compact_header_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def is_sensitive_header(name: str) -> bool:
    normalized = name.strip().lower()
    compacted = _compact_header_name(normalized)
    return (
        normalized in _SENSITIVE_HEADER_NAMES
        or compacted
        in {
            "authorization",
            "proxyauthorization",
            "auth",
            "authentication",
            "xauth",
            "xapikey",
            "apikey",
            "session",
            "sessionid",
            "sid",
            "jwt",
            "csrf",
            "csrftoken",
            "xsrf",
            "xsrftoken",
            "captcha",
            "mfa",
            "otp",
        }
        or any(marker in compacted for marker in _SENSITIVE_HEADER_SUBSTRINGS)
    )


def redact_sensitive_headers(headers: Any) -> Any:
    if not isinstance(headers, dict):
        return headers
    return {
        key: REDACTED_VALUE if is_sensitive_header(str(key)) else redact_sensitive_data(value)
        for key, value in headers.items()
    }


def sanitize_persisted_text(text: str) -> str:
    return _UNSAFE_CONTROL_CHARS_RE.sub("", text)


def redact_sensitive_text(text: str) -> str:
    text = sanitize_persisted_text(text)
    redacted = _AUTH_SCHEME_RE.sub(lambda match: f"{match.group(1)} {REDACTED_VALUE}", text)
    redacted = _QUOTED_SECRET_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{REDACTED_VALUE}{match.group(2)}",
        redacted,
    )
    redacted = _SPACED_SECRET_RE.sub(lambda match: f"{match.group(1)}{REDACTED_VALUE}", redacted)
    redacted = _BARE_SECRET_RE.sub(lambda match: f"{match.group(1)}{REDACTED_VALUE}", redacted)
    return _redact_playwright_like_command(redacted)


def _redact_structured_text(text: str) -> str | None:
    stripped = text.strip()
    if not (
        (stripped.startswith("{") and stripped.endswith("}"))
        or (stripped.startswith("[") and stripped.endswith("]"))
    ):
        return None
    try:
        parsed = json.loads(stripped)
    except Exception:
        return None
    return json.dumps(redact_sensitive_data(parsed), ensure_ascii=False, default=str)


def _redact_playwright_like_command(text: str) -> str:
    redacted = _PLAYWRIGHT_TWO_QUOTED_ARGS_RE.sub(
        lambda match: (
            f"{match.group(1)} {match.group(2)}{match.group(3)}{match.group(4)} "
            f"{match.group(5)}{REDACTED_VALUE}{match.group(7)}"
        ),
        text,
    )
    redacted = _PLAYWRIGHT_UNQUOTED_SELECTOR_QUOTED_VALUE_RE.sub(
        lambda match: (
            f"{match.group(1)} {match.group(2)} "
            f"{match.group(3)}{REDACTED_VALUE}{match.group(5)}"
        ),
        redacted,
    )
    return _PLAYWRIGHT_UNQUOTED_VALUE_RE.sub(
        lambda match: f"{match.group(1)} {match.group(2)} {REDACTED_VALUE}",
        redacted,
    )


def redact_sensitive_data(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for key, child in value.items():
            safe_key = sanitize_persisted_text(key) if isinstance(key, str) else key
            key_text = str(safe_key)
            if key_text.lower() in _HEADER_CONTAINER_KEYS:
                redacted[safe_key] = redact_sensitive_headers(child)
            elif key_text.lower() in _SAFE_SENSITIVE_METADATA_KEYS:
                redacted[safe_key] = redact_sensitive_data(child)
            elif is_sensitive_header(key_text):
                redacted[safe_key] = (
                    redact_sensitive_data(child)
                    if isinstance(child, (dict, list))
                    else REDACTED_VALUE
                )
            else:
                redacted[safe_key] = redact_sensitive_data(child)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive_data(item) for item in value]
    if isinstance(value, str):
        structured = _redact_structured_text(value)
        if structured is not None:
            return structured
        return redact_sensitive_text(value)
    return value


def redact_json_text(text: str | None) -> str | None:
    if not text:
        return text
    try:
        parsed = json.loads(text)
    except Exception:
        return redact_sensitive_text(text)
    return json.dumps(redact_sensitive_data(parsed), ensure_ascii=False, default=str)

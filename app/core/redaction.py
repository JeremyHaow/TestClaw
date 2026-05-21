import json
import re
from typing import Any


REDACTED_VALUE = "[REDACTED]"

_SENSITIVE_HEADER_NAMES = {
    "authorization",
    "proxy-authorization",
    "x-api-key",
    "api-key",
}
_HEADER_CONTAINER_KEYS = {
    "headers",
    "request_headers",
    "auth_headers",
    "custom_headers",
}


def _compact_header_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def is_sensitive_header(name: str) -> bool:
    normalized = name.strip().lower()
    compacted = _compact_header_name(normalized)
    return (
        normalized in _SENSITIVE_HEADER_NAMES
        or compacted in {"authorization", "proxyauthorization", "xapikey", "apikey"}
        or "token" in compacted
        or "cookie" in compacted
        or "password" in compacted
        or "passwd" in compacted
        or "secret" in compacted
        or "credential" in compacted
    )


def redact_sensitive_headers(headers: Any) -> Any:
    if not isinstance(headers, dict):
        return headers
    return {
        key: REDACTED_VALUE if is_sensitive_header(str(key)) else redact_sensitive_data(value)
        for key, value in headers.items()
    }


def redact_sensitive_data(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for key, child in value.items():
            key_text = str(key)
            if is_sensitive_header(key_text):
                redacted[key] = REDACTED_VALUE
            elif key_text.lower() in _HEADER_CONTAINER_KEYS:
                redacted[key] = redact_sensitive_headers(child)
            else:
                redacted[key] = redact_sensitive_data(child)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive_data(item) for item in value]
    return value


def redact_json_text(text: str | None) -> str | None:
    if not text:
        return text
    try:
        parsed = json.loads(text)
    except Exception:
        return text
    return json.dumps(redact_sensitive_data(parsed), ensure_ascii=False, default=str)

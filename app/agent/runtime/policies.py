from __future__ import annotations

from typing import Any

from app.agent.api_scope import SAFE_API_METHODS, WRITE_API_METHODS, _normalize_method
from app.core.redaction import redact_sensitive_data, redact_sensitive_text

MAX_RUNTIME_TEXT = 2000
MAX_RUNTIME_JSON_TEXT = 8000
HIGH_RISK_UI_ACTIONS = {"run_code", "run-code", "eval", "evaluate"}


def redact_runtime_payload(value: Any) -> Any:
    return redact_sensitive_data(value)


def redact_runtime_text(value: Any, *, limit: int = MAX_RUNTIME_TEXT) -> str:
    return redact_sensitive_text(str(value or ""))[:limit]


def api_method_allowed(method: Any, policy: str = "safe_read_only") -> bool:
    normalized = _normalize_method(method)
    if normalized in SAFE_API_METHODS:
        return True
    if normalized in WRITE_API_METHODS:
        return str(policy or "safe_read_only").lower() == "write_allowed"
    return False


def ui_action_allowed(action_type: Any, *, risk: str | None = None, allow_high_risk: bool = False) -> bool:
    normalized = str(action_type or "").strip().lower().replace("_", "-")
    if normalized in HIGH_RISK_UI_ACTIONS:
        return allow_high_risk and str(risk or "").lower() == "high"
    return True


def compact_runtime_value(value: Any, *, limit: int = MAX_RUNTIME_JSON_TEXT) -> Any:
    redacted = redact_runtime_payload(value)
    text = str(redacted)
    if len(text) <= limit:
        return redacted
    return redact_runtime_text(text, limit=limit)

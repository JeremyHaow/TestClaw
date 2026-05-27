from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class FailureDefinition:
    failure_type: str
    layer: str
    retryable: bool
    human_required: bool
    report_category: str
    default_next_action: str
    severity: str = "medium"


_FAILURES: tuple[FailureDefinition, ...] = (
    FailureDefinition("auth_failure", "api", False, True, "authentication", "ask_human", "high"),
    FailureDefinition("network_error", "api", True, False, "environment", "retry_same_action", "high"),
    FailureDefinition("timeout", "shared", True, False, "environment", "retry_same_action", "medium"),
    FailureDefinition("backend_error", "api", False, False, "server_error", "report", "critical"),
    FailureDefinition("schema_contract", "api", False, False, "contract", "report", "medium"),
    FailureDefinition("assertion_failure", "shared", False, False, "assertion", "report", "medium"),
    FailureDefinition("safe_write_blocked", "api", False, False, "safety_guardrail", "replan_api", "medium"),
    FailureDefinition("dependency_missing", "api", False, False, "missing_dependency", "replan_api", "medium"),
    FailureDefinition("environment_blocked", "api", False, True, "environment", "ask_human", "high"),
    FailureDefinition("ui_locator_missing", "ui", False, False, "locator", "replan_ui", "medium"),
    FailureDefinition("ui_assertion_failure", "ui", False, False, "assertion", "replan_ui", "medium"),
    FailureDefinition("navigation_blocked", "ui", True, False, "navigation", "retry_same_action", "medium"),
    FailureDefinition("ui_setup_failed", "ui", False, True, "setup", "ask_human", "high"),
    FailureDefinition("ui_high_risk_action_blocked", "ui", False, True, "safety_guardrail", "ask_human", "high"),
    FailureDefinition("ui_action_blocked", "ui", False, False, "safety_guardrail", "replan_ui", "medium"),
    FailureDefinition("ui_command_skipped", "ui", False, False, "execution_skipped", "report", "low"),
    FailureDefinition("ui_command_failed", "ui", False, False, "browser_execution", "report", "medium"),
    FailureDefinition("artifact_missing", "ui", False, False, "artifact", "replan_ui", "medium"),
    FailureDefinition("unknown_failure", "shared", False, False, "unknown", "report", "medium"),
)

_BY_TYPE = {item.failure_type: item for item in _FAILURES}

_ALIASES = {
    "api_assertion": "assertion_failure",
    "validation_contract": "assertion_failure",
    "backend_validation_contract": "assertion_failure",
    "safe_write_gate_blocked": "safe_write_blocked",
    "crud_skill_blocked": "safe_write_blocked",
    "path_param_unresolved": "dependency_missing",
    "missing_dependency": "dependency_missing",
    "environment_not_executable": "environment_blocked",
    "execution_budget_exhausted": "environment_blocked",
}


def normalize_failure_type(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    return _ALIASES.get(text, text)


def failure_definition(value: Any) -> FailureDefinition:
    normalized = normalize_failure_type(value) or "unknown_failure"
    return _BY_TYPE.get(normalized, _BY_TYPE["unknown_failure"])


def failure_taxonomy_payload() -> dict[str, dict[str, Any]]:
    return {item.failure_type: asdict(item) for item in _FAILURES}


def failure_is_retryable(value: Any) -> bool:
    return failure_definition(value).retryable


def failure_requires_human(value: Any) -> bool:
    return failure_definition(value).human_required


def report_category_for_failure(value: Any) -> str:
    return failure_definition(value).report_category


def next_action_hint(value: Any, *, layer: str | None = None) -> str:
    definition = failure_definition(value)
    if definition.failure_type == "unknown_failure" and layer == "ui":
        return "replan_ui"
    if definition.failure_type == "unknown_failure" and layer == "api":
        return "report"
    return definition.default_next_action


def classify_api_failure(
    *,
    status_code: int | None = None,
    error: Any = None,
    raw_failure_type: Any = None,
    assertion_results: list[dict[str, Any]] | None = None,
    skipped: bool = False,
    method: str | None = None,
) -> str | None:
    raw = normalize_failure_type(raw_failure_type)
    if raw:
        return raw
    error_text = str(error or "").lower()
    if error_text:
        if "timeout" in error_text or "timed out" in error_text:
            return "timeout"
        if any(marker in error_text for marker in ("connect", "connection", "network", "dns", "unreachable")):
            return "network_error"
        return "network_error"
    if skipped and str(method or "").upper() in {"POST", "PUT", "PATCH", "DELETE"}:
        return "safe_write_blocked"
    if status_code in {401, 403}:
        return "auth_failure"
    if status_code and status_code >= 500:
        return "backend_error"
    for assertion in assertion_results or []:
        if assertion.get("type") == "schema" and assertion.get("passed") is False:
            return "schema_contract"
    if any(assertion.get("passed") is False for assertion in assertion_results or []):
        return "assertion_failure"
    return None


def classify_ui_failure(result: dict[str, Any]) -> str | None:
    raw = normalize_failure_type(result.get("failure_type"))
    if raw:
        return raw
    if result.get("status") == "blocked":
        return "ui_high_risk_action_blocked" if result.get("risk") == "high_risk" else "ui_action_blocked"
    if result.get("status") == "skipped":
        return "ui_command_skipped"
    if result.get("passed") is not False and int(result.get("status_code") or 0) == 0:
        return None
    stderr = str(result.get("stderr") or "").lower()
    if "timeout" in stderr or "timed out" in stderr:
        return "timeout"
    if any(marker in stderr for marker in ("not found", "locator", "strict mode violation", "does not match any elements")):
        return "ui_locator_missing"
    if "navigation" in stderr:
        return "navigation_blocked"
    if "snapshot did not contain" in stderr:
        return "ui_assertion_failure"
    if "screenshot file was not created" in stderr:
        return "artifact_missing"
    return "ui_command_failed"

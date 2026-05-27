from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from app.agent.api_scope import (
    SAFE_API_METHODS,
    WRITE_API_METHODS,
    _normalize_method,
    _normalize_path,
    _path_template_re,
)
from app.agent.tool_registry import record_tool_call, tool_capabilities_by_name
from app.core.redaction import redact_sensitive_data

AGENT_ACTION_SCHEMA_VERSION = "2026-05-25"
AGENT_EXECUTION_PROTOCOL_VERSION = "2026-05-27"
AGENT_ACTION_CONTRACT_SOURCE = "agent_action_contract"
AGENT_PROTOCOL_MAX_RECORDS = 1000
API_ACTION_SCOPES = {
    "all_documented_safe_methods",
    "focused_documented_endpoints",
    "sampled_contract",
    "none",
}


class AgentActionDiagnostic(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: str
    severity: str = "warning"
    action: str = "recorded"
    detail: str
    tool_name: str | None = None
    field: str | None = None
    method: str | None = None
    path: str | None = None


class AgentAction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: str = AGENT_ACTION_SCHEMA_VERSION
    action_id: str
    tool_name: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    safety_constraints: list[str] = Field(default_factory=list)
    expected_observation: str = ""
    reason: str = ""
    source: str = "llm"


class ValidatedAgentAction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: str = AGENT_ACTION_SCHEMA_VERSION
    action_id: str
    tool_name: str
    layer: str = "unknown"
    skill: str = "unknown"
    risk: str = "unknown"
    allowed: bool = True
    inputs: dict[str, Any] = Field(default_factory=dict)
    safety_constraints: list[str] = Field(default_factory=list)
    expected_observation: str = ""
    reason: str = ""
    policy: str = "safe_read_only"
    source: str = AGENT_ACTION_CONTRACT_SOURCE
    diagnostics: list[AgentActionDiagnostic] = Field(default_factory=list)


class AgentActionObservation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: str = AGENT_ACTION_SCHEMA_VERSION
    action_id: str
    tool_name: str
    stage: str
    layer: str = "unknown"
    risk: str = "unknown"
    status: str
    policy: str = "safe_read_only"
    inputs: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    observation: str = ""
    timestamp: str


class AgentToolCall(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: str = AGENT_EXECUTION_PROTOCOL_VERSION
    tool_call_id: str
    tool_name: str
    layer: str
    status: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    elapsed_ms: float | None = None
    action_id: str | None = None
    case_index: int | None = None
    case_title: str | None = None
    timestamp: str


class AgentEvidence(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: str = AGENT_EXECUTION_PROTOCOL_VERSION
    evidence_id: str
    kind: str
    stage: str
    layer: str
    title: str
    status: str
    summary: str = ""
    uri: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: str


class AgentObservation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: str = AGENT_EXECUTION_PROTOCOL_VERSION
    observation_id: str
    stage: str
    layer: str
    tool_name: str
    status: str
    outcome: str
    summary: str
    action_id: str | None = None
    failure_type: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    tool_call_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: str


class AgentEvaluation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: str = AGENT_EXECUTION_PROTOCOL_VERSION
    evaluation_id: str
    stage: str
    sufficient_evidence: bool
    outcome: str
    next_action: str
    confidence: str = "unknown"
    reason: str = ""
    failure_type: str | None = None
    missing_evidence: list[str] = Field(default_factory=list)
    replan_hint: str = ""
    observation_ids: list[str] = Field(default_factory=list)
    timestamp: str


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any, *, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _compact_jsonable(value: Any, *, limit: int = 1200) -> Any:
    value = redact_sensitive_data(value)
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return _text(value, limit=limit)
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    return _text(text, limit=limit)


def _protocol_id(prefix: str, *parts: Any) -> str:
    raw = json.dumps(redact_sensitive_data(parts), ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _append_protocol_record(
    state: dict[str, Any],
    key: str,
    payload: dict[str, Any],
    *,
    limit: int = AGENT_PROTOCOL_MAX_RECORDS,
) -> dict[str, Any]:
    safe_payload = redact_sensitive_data(payload)
    records = state.setdefault(key, [])
    records.append(safe_payload)
    if len(records) > limit:
        del records[:-limit]
    return safe_payload


def _protocol_summary(state: dict[str, Any]) -> dict[str, Any]:
    observations = [item for item in state.get("agent_observations") or [] if isinstance(item, dict)]
    evidence = [item for item in state.get("agent_evidence") or [] if isinstance(item, dict)]
    by_layer: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_failure_type: dict[str, int] = {}
    by_evidence_kind: dict[str, int] = {}
    for observation in observations:
        layer = str(observation.get("layer") or "unknown")
        status = str(observation.get("status") or "unknown")
        by_layer[layer] = by_layer.get(layer, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1
        failure_type = observation.get("failure_type")
        if failure_type:
            key = str(failure_type)
            by_failure_type[key] = by_failure_type.get(key, 0) + 1
    for item in evidence:
        kind = str(item.get("kind") or "unknown")
        by_evidence_kind[kind] = by_evidence_kind.get(kind, 0) + 1

    summary = {
        "schema_version": AGENT_EXECUTION_PROTOCOL_VERSION,
        "observation_total": len(observations),
        "evidence_total": len(evidence),
        "by_layer": by_layer,
        "by_status": by_status,
        "by_failure_type": by_failure_type,
        "by_evidence_kind": by_evidence_kind,
    }
    # This summary only contains aggregate counts. Do not pass it through the
    # generic redactor, because taxonomy keys like "auth_failure" would make
    # their numeric counts look secret-bearing.
    state["agent_protocol_summary"] = summary
    return state["agent_protocol_summary"]


def _diagnostic(
    kind: str,
    detail: str,
    *,
    severity: str = "warning",
    action: str = "recorded",
    tool_name: str | None = None,
    field: str | None = None,
    method: str | None = None,
    path: str | None = None,
) -> AgentActionDiagnostic:
    return AgentActionDiagnostic(
        kind=kind,
        severity=severity,
        action=action,
        detail=detail,
        tool_name=tool_name,
        field=field,
        method=method,
        path=path,
    )


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "tool"


def _policy_methods(execution_policy: str) -> tuple[set[str], bool]:
    policy = str(execution_policy or "safe_read_only").strip().lower()
    if policy == "write_allowed":
        return set(SAFE_API_METHODS | WRITE_API_METHODS), True
    return set(SAFE_API_METHODS), False


def _schema_endpoints(parsed_api_schema: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [endpoint for endpoint in (parsed_api_schema or []) if isinstance(endpoint, dict)]


def _schema_endpoint_match(
    method: str,
    path: str,
    schema_endpoints: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not method or not path:
        return None
    for endpoint in schema_endpoints:
        endpoint_method = _normalize_method(endpoint.get("method"))
        endpoint_path = _normalize_path(endpoint.get("path"))
        if endpoint_method != method or not endpoint_path:
            continue
        if path == endpoint_path or _path_template_re(endpoint_path).match(path):
            return endpoint
    return None


def _strategy_selection(strategy: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(strategy, dict):
        return {}
    selection = strategy.get("endpoint_selection")
    return selection if isinstance(selection, dict) else {}


def _strategy_method_policy(strategy: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(strategy, dict):
        return {}
    policy = strategy.get("method_policy")
    return policy if isinstance(policy, dict) else {}


def _normalize_endpoint_refs(
    value: Any,
    *,
    schema_endpoints: list[dict[str, Any]],
    allowed_methods: set[str],
    diagnostics: list[AgentActionDiagnostic],
    tool_name: str,
    field: str,
) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    schema_backed = bool(schema_endpoints)
    for item in _as_list(value):
        if not isinstance(item, dict):
            diagnostics.append(
                _diagnostic(
                    "invalid_action_endpoint_reference",
                    "Endpoint reference was not an object and was ignored.",
                    action="dropped",
                    tool_name=tool_name,
                    field=field,
                )
            )
            continue
        method = _normalize_method(item.get("method"))
        path = _normalize_path(item.get("path") or item.get("endpoint") or item.get("url"))
        if method not in allowed_methods:
            diagnostics.append(
                _diagnostic(
                    "method_blocked_by_policy",
                    f"{method} {path or ''}".strip()
                    + " is not allowed by the local execution policy.",
                    severity="error",
                    action="blocked",
                    tool_name=tool_name,
                    field=field,
                    method=method,
                    path=path or None,
                )
            )
            continue
        endpoint = _schema_endpoint_match(method, path, schema_endpoints)
        if schema_backed and endpoint is None:
            diagnostics.append(
                _diagnostic(
                    "out_of_schema_endpoint",
                    f"{method} {path} is not present in the loaded OpenAPI schema.",
                    severity="error",
                    action="blocked",
                    tool_name=tool_name,
                    field=field,
                    method=method,
                    path=path,
                )
            )
            continue
        canonical_path = _normalize_path(endpoint.get("path")) if endpoint else path
        key = (method, canonical_path)
        if key in seen:
            continue
        seen.add(key)
        refs.append({"method": method, "path": canonical_path})
    return refs


def _normalize_method_policy_input(
    value: Any,
    *,
    allowed_methods: set[str],
    write_allowed: bool,
    diagnostics: list[AgentActionDiagnostic],
    tool_name: str,
) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    requested = [
        _normalize_method(item)
        for item in _as_list(raw.get("allowed_methods"))
        if _normalize_method(item)
    ]
    normalized_allowed = sorted(
        {method for method in (requested or allowed_methods) if method in allowed_methods}
    )
    for method in sorted({method for method in requested if method not in allowed_methods}):
        diagnostics.append(
            _diagnostic(
                "method_blocked_by_policy",
                f"Model action requested {method}, but policy does not allow it.",
                severity="error",
                action="blocked",
                tool_name=tool_name,
                field="method_policy.allowed_methods",
                method=method,
            )
        )
    requested_write_allowed = bool(raw.get("write_allowed"))
    if requested_write_allowed and not write_allowed:
        diagnostics.append(
            _diagnostic(
                "write_policy_overridden",
                "Model action requested write_allowed=true, but local policy is read-only.",
                severity="error",
                action="blocked",
                tool_name=tool_name,
                field="method_policy.write_allowed",
            )
        )
    blocked = sorted(set(WRITE_API_METHODS) - set(normalized_allowed))
    return {
        "allowed_methods": normalized_allowed,
        "blocked_methods": blocked,
        "write_allowed": write_allowed,
    }


def _normalize_action_inputs(
    action: AgentAction,
    *,
    strategy: dict[str, Any] | None,
    schema_endpoints: list[dict[str, Any]],
    execution_policy: str,
    diagnostics: list[AgentActionDiagnostic],
) -> dict[str, Any]:
    if not isinstance(action.inputs, dict):
        diagnostics.append(
            _diagnostic(
                "invalid_action_inputs",
                "Tool action inputs must be a JSON object; an empty object was used.",
                severity="error",
                action="normalized",
                tool_name=action.tool_name,
                field="inputs",
            )
        )
        inputs: dict[str, Any] = {}
    else:
        inputs = dict(action.inputs)

    tool_name = action.tool_name
    strategy_selection = _strategy_selection(strategy)
    strategy_method_policy = _strategy_method_policy(strategy)
    allowed_methods, write_allowed = _policy_methods(execution_policy)

    if tool_name == "api.derive_schema_requests":
        inputs.setdefault("scope", (strategy or {}).get("coverage_scope") or "none")
        inputs.setdefault("include", strategy_selection.get("include") or [])
        inputs.setdefault("exclude", strategy_selection.get("exclude") or [])
        inputs.setdefault("method_policy", strategy_method_policy)
        if strategy_selection.get("budget_behavior"):
            inputs.setdefault("budget_behavior", strategy_selection.get("budget_behavior"))

    if "method_policy" in inputs:
        inputs["method_policy"] = _normalize_method_policy_input(
            inputs.get("method_policy"),
            allowed_methods=allowed_methods,
            write_allowed=write_allowed,
            diagnostics=diagnostics,
            tool_name=tool_name,
        )

    if "method" in inputs:
        inputs["method"] = _normalize_method(inputs.get("method"))

    for path_key in ("path", "endpoint"):
        if path_key in inputs:
            inputs[path_key] = _normalize_path(inputs.get(path_key))
    if "url" in inputs and "path" not in inputs:
        normalized_path = _normalize_path(inputs.get("url"))
        if normalized_path:
            inputs["path"] = normalized_path

    if "scope" in inputs:
        scope = str(inputs.get("scope") or "").strip().lower()
        if scope not in API_ACTION_SCOPES:
            diagnostics.append(
                _diagnostic(
                    "unknown_action_scope",
                    f"Unknown action scope '{scope}' was replaced with 'none'.",
                    action="normalized",
                    tool_name=tool_name,
                    field="inputs.scope",
                )
            )
            scope = "none"
        inputs["scope"] = scope

    for key in ("include", "exclude"):
        if key in inputs:
            inputs[key] = _normalize_endpoint_refs(
                inputs.get(key),
                schema_endpoints=schema_endpoints,
                allowed_methods=allowed_methods,
                diagnostics=diagnostics,
                tool_name=tool_name,
                field=f"inputs.{key}",
            )

    headers = inputs.get("headers")
    if headers is not None and not isinstance(headers, dict):
        diagnostics.append(
            _diagnostic(
                "invalid_action_headers",
                "Headers must be a JSON object and were removed.",
                action="normalized",
                tool_name=tool_name,
                field="inputs.headers",
            )
        )
        inputs.pop("headers", None)

    return redact_sensitive_data(inputs)


def _validate_api_action(
    action: AgentAction,
    *,
    inputs: dict[str, Any],
    schema_endpoints: list[dict[str, Any]],
    execution_policy: str,
    diagnostics: list[AgentActionDiagnostic],
) -> bool:
    allowed_methods, _write_allowed = _policy_methods(execution_policy)
    allowed = True

    method = _normalize_method(inputs.get("method")) if inputs.get("method") else ""
    path = _normalize_path(inputs.get("path") or inputs.get("endpoint") or inputs.get("url"))
    if method and method not in SAFE_API_METHODS | WRITE_API_METHODS:
        diagnostics.append(
            _diagnostic(
                "invalid_api_method",
                f"HTTP method '{method}' is not supported by the local API runtime.",
                severity="error",
                action="blocked",
                tool_name=action.tool_name,
                field="inputs.method",
                method=method,
                path=path or None,
            )
        )
        allowed = False
    elif method and method not in allowed_methods and action.tool_name != "api.safe_write_gate":
        diagnostics.append(
            _diagnostic(
                "method_blocked_by_policy",
                f"{method} is blocked by execution policy '{execution_policy}'.",
                severity="error",
                action="blocked",
                tool_name=action.tool_name,
                field="inputs.method",
                method=method,
                path=path or None,
            )
        )
        allowed = False

    if method and path and schema_endpoints and action.tool_name != "api.safe_write_gate":
        endpoint = _schema_endpoint_match(method, path, schema_endpoints)
        if endpoint is None:
            diagnostics.append(
                _diagnostic(
                    "out_of_schema_endpoint",
                    f"{method} {path} is not present in the loaded OpenAPI schema.",
                    severity="error",
                    action="blocked",
                    tool_name=action.tool_name,
                    field="inputs.path",
                    method=method,
                    path=path,
                )
            )
            allowed = False

    if action.tool_name == "api.derive_schema_requests":
        scope = str(inputs.get("scope") or "none")
        include = _as_list(inputs.get("include"))
        if scope == "all_documented_safe_methods" and not schema_endpoints:
            diagnostics.append(
                _diagnostic(
                    "missing_schema_for_action",
                    "Schema-derived request selection requires a loaded OpenAPI schema.",
                    severity="error",
                    action="blocked",
                    tool_name=action.tool_name,
                    field="inputs.scope",
                )
            )
            allowed = False
        if scope in {"focused_documented_endpoints", "sampled_contract"} and not include:
            diagnostics.append(
                _diagnostic(
                    "missing_action_endpoint_include",
                    f"Action scope '{scope}' requires at least one validated endpoint.",
                    severity="error",
                    action="blocked",
                    tool_name=action.tool_name,
                    field="inputs.include",
                )
            )
            allowed = False

    if action.tool_name == "api.http_request" and not (method and (path or inputs.get("url"))):
        diagnostics.append(
            _diagnostic(
                "invalid_http_request_action",
                "api.http_request actions must include method and path or url.",
                severity="error",
                action="blocked",
                tool_name=action.tool_name,
                field="inputs",
            )
        )
        allowed = False

    return allowed


def _validate_ui_action(
    action: AgentAction,
    *,
    inputs: dict[str, Any],
    diagnostics: list[AgentActionDiagnostic],
) -> bool:
    if action.tool_name != "ui.playwright_cli":
        return True
    command = inputs.get("command")
    if command is not None and not isinstance(command, str):
        diagnostics.append(
            _diagnostic(
                "invalid_ui_command",
                "ui.playwright_cli command must be a string.",
                severity="error",
                action="blocked",
                tool_name=action.tool_name,
                field="inputs.command",
            )
        )
        return False
    return True


def _action_from_plan_step(
    item: Any,
    *,
    index: int,
    strategy: dict[str, Any] | None,
) -> AgentAction | None:
    if not isinstance(item, dict):
        return None
    tool_name = _text(item.get("tool_name") or item.get("tool"), limit=160)
    if not tool_name:
        return None
    action_id = _text(item.get("action_id"), limit=160)
    if not action_id:
        action_id = f"action-{index + 1}-{_slug(tool_name)}"
    reason = _text(item.get("reason") or (strategy or {}).get("reason"), limit=360)
    inputs = item.get("inputs") if isinstance(item.get("inputs"), dict) else {}
    safety_constraints = [
        _text(value, limit=120)
        for value in _as_list(item.get("safety_constraints"))
        if _text(value, limit=120)
    ]
    return AgentAction(
        action_id=action_id,
        tool_name=tool_name,
        inputs=inputs,
        safety_constraints=safety_constraints,
        expected_observation=_text(item.get("expected_observation"), limit=300),
        reason=reason,
        source=_text((strategy or {}).get("source") or item.get("source") or "llm", limit=80),
    )


def validate_agent_action_plan(
    tool_plan: Any,
    *,
    strategy: dict[str, Any] | None = None,
    parsed_api_schema: list[dict[str, Any]] | None = None,
    execution_policy: str = "safe_read_only",
) -> list[dict[str, Any]]:
    capabilities = tool_capabilities_by_name()
    schema_endpoints = _schema_endpoints(parsed_api_schema)
    actions: list[dict[str, Any]] = []

    for index, item in enumerate(_as_list(tool_plan)):
        diagnostics: list[AgentActionDiagnostic] = []
        action = _action_from_plan_step(item, index=index, strategy=strategy)
        if action is None:
            diagnostics.append(
                _diagnostic(
                    "invalid_agent_action",
                    "Tool plan step must be a JSON object with tool_name.",
                    severity="error",
                    action="blocked",
                )
            )
            continue

        capability = capabilities.get(action.tool_name)
        allowed = capability is not None
        if capability is None:
            diagnostics.append(
                _diagnostic(
                    "unknown_tool_name",
                    f"Unknown tool '{action.tool_name}' is not registered.",
                    severity="error",
                    action="blocked",
                    tool_name=action.tool_name,
                )
            )
            layer = "unknown"
            skill = "unknown"
            risk = "unknown"
        else:
            layer = str(capability.get("layer") or "unknown")
            skill = str(capability.get("skill") or "unknown")
            risk = str(capability.get("risk") or "unknown")

        inputs = _normalize_action_inputs(
            action,
            strategy=strategy,
            schema_endpoints=schema_endpoints,
            execution_policy=execution_policy,
            diagnostics=diagnostics,
        )
        if action.tool_name.startswith("api."):
            allowed = (
                allowed
                and _validate_api_action(
                    action,
                    inputs=inputs,
                    schema_endpoints=schema_endpoints,
                    execution_policy=execution_policy,
                    diagnostics=diagnostics,
                )
            )
        elif action.tool_name.startswith("ui."):
            allowed = allowed and _validate_ui_action(
                action,
                inputs=inputs,
                diagnostics=diagnostics,
            )

        validated = ValidatedAgentAction(
            action_id=action.action_id,
            tool_name=action.tool_name,
            layer=layer,
            skill=skill,
            risk=risk,
            allowed=allowed,
            inputs=inputs,
            safety_constraints=action.safety_constraints,
            expected_observation=action.expected_observation,
            reason=action.reason,
            policy=str(execution_policy or "safe_read_only"),
            source=action.source,
            diagnostics=diagnostics,
        )
        actions.append(redact_sensitive_data(validated.model_dump(mode="json")))

    return actions


def _action_observation_text(
    action: dict[str, Any],
    *,
    status: str,
    output_summary: dict[str, Any],
) -> str:
    if status == "blocked":
        count = len(action.get("diagnostics") or [])
        return f"blocked by local validator; diagnostic_count={count}"
    if output_summary.get("selected_total") is not None:
        return (
            f"{status}; selected_total={output_summary.get('selected_total')}, "
            f"candidate_total={output_summary.get('candidate_total')}"
        )
    if output_summary.get("diagnostic_count") is not None:
        return f"{status}; diagnostic_count={output_summary.get('diagnostic_count')}"
    return status


def record_agent_action_observation(
    state: dict[str, Any],
    action: dict[str, Any],
    *,
    stage: str,
    status: str | None = None,
    output_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(action, dict):
        return {}
    status = status or ("validated" if action.get("allowed") else "blocked")
    diagnostics = _as_list(action.get("diagnostics"))
    output = {
        "allowed": bool(action.get("allowed")),
        "risk": action.get("risk"),
        "policy": action.get("policy"),
        "diagnostic_count": len(diagnostics),
        "expected_observation": action.get("expected_observation"),
    }
    if output_summary:
        output.update(output_summary)
    output = redact_sensitive_data(output)
    observation_text = _action_observation_text(action, status=status, output_summary=output)
    observation = AgentActionObservation(
        action_id=str(action.get("action_id") or ""),
        tool_name=str(action.get("tool_name") or ""),
        stage=stage,
        layer=str(action.get("layer") or "unknown"),
        risk=str(action.get("risk") or "unknown"),
        status=status,
        policy=str(action.get("policy") or "safe_read_only"),
        inputs=redact_sensitive_data(action.get("inputs") or {}),
        output=output,
        diagnostics=redact_sensitive_data(diagnostics),
        observation=observation_text,
        timestamp=_utc_now_iso(),
    )
    payload = redact_sensitive_data(observation.model_dump(mode="json"))
    state.setdefault("agent_action_observations", []).append(payload)
    if len(state["agent_action_observations"]) > 500:
        del state["agent_action_observations"][:-500]

    if diagnostics:
        state.setdefault("agent_action_diagnostics", []).extend(redact_sensitive_data(diagnostics))

    record_tool_call(
        state,
        tool_name=str(action.get("tool_name") or "unknown"),
        layer=str(action.get("layer") or "unknown"),
        status=status,
        input_summary={
            "action_id": action.get("action_id"),
            "stage": stage,
            "validated_inputs": action.get("inputs") or {},
            "safety_constraints": action.get("safety_constraints") or [],
        },
        output_summary=output,
        metadata={
            "actor": "supervisor_planner",
            "reason": action.get("reason")
            or "Validate model-selected action before local execution.",
            "action_runtime_stage": stage,
            "next_decision": "execute_validated_tool"
            if action.get("allowed")
            else "surface_guardrail_diagnostic",
        },
    )
    return payload


def _api_failure_type(result: dict[str, Any]) -> str | None:
    failure_type = result.get("failure_type") or result.get("skip_type")
    if failure_type:
        return str(failure_type)
    if result.get("error"):
        text = str(result.get("error") or "").lower()
        if "timeout" in text or "timed out" in text:
            return "timeout"
        if any(marker in text for marker in ("connect", "network", "dns", "name resolution")):
            return "network_error"
        return "api_request_error"
    assertions = [item for item in result.get("assertion_results") or [] if isinstance(item, dict)]
    if any(item.get("passed") is False for item in assertions):
        return "assertion_failure"
    if result.get("passed") is False:
        status = result.get("status_code")
        try:
            status_int = int(status)
        except Exception:
            status_int = 0
        if status_int in {401, 403}:
            return "auth_failure"
        if status_int >= 500:
            return "backend_error"
        return "api_assertion"
    return None


def _ui_failure_type(result: dict[str, Any]) -> str | None:
    if result.get("failure_type"):
        return str(result.get("failure_type"))
    if result.get("status") == "blocked":
        if result.get("risk") == "high_risk":
            return "ui_high_risk_action_blocked"
        return "ui_action_blocked"
    if result.get("status") == "skipped":
        return "ui_command_skipped"
    if result.get("passed") is not False and int(result.get("status_code") or 0) == 0:
        return None
    stderr = str(result.get("stderr") or "").lower()
    if "timeout" in stderr or "timed out" in stderr:
        return "timeout"
    if any(marker in stderr for marker in ("not found", "locator", "strict mode violation")):
        return "ui_locator_missing"
    if "navigation" in stderr:
        return "navigation_blocked"
    if "snapshot did not contain" in stderr:
        return "ui_assertion_failure"
    if "screenshot file was not created" in stderr:
        return "artifact_missing"
    return "ui_command_failed"


def _outcome_from_status(status: str, failure_type: str | None = None) -> str:
    if status in {"success", "passed", "done"}:
        return "passed"
    if status == "blocked":
        return "blocked"
    if status == "skipped":
        return "blocked" if failure_type else "skipped"
    if status in {"failed", "error"}:
        return "failed"
    return status or "unknown"


def _append_tool_call_protocol(
    state: dict[str, Any],
    *,
    tool_name: str,
    layer: str,
    status: str,
    inputs: dict[str, Any] | None = None,
    outputs: dict[str, Any] | None = None,
    elapsed_ms: float | None = None,
    action_id: str | None = None,
    case_index: int | None = None,
    case_title: str | None = None,
) -> str:
    timestamp = _utc_now_iso()
    tool_call = AgentToolCall(
        tool_call_id=_protocol_id(
            "tool-call",
            tool_name,
            layer,
            status,
            inputs,
            outputs,
            case_index,
            case_title,
            timestamp,
        ),
        tool_name=tool_name,
        layer=layer,
        status=status,
        inputs=redact_sensitive_data(inputs or {}),
        outputs=redact_sensitive_data(outputs or {}),
        elapsed_ms=elapsed_ms,
        action_id=action_id,
        case_index=case_index,
        case_title=case_title,
        timestamp=timestamp,
    )
    payload = _append_protocol_record(
        state,
        "agent_tool_calls",
        tool_call.model_dump(mode="json", exclude_none=True),
    )
    return str(payload["tool_call_id"])


def _append_evidence_protocol(
    state: dict[str, Any],
    *,
    kind: str,
    stage: str,
    layer: str,
    title: str,
    status: str,
    summary: str = "",
    uri: str | None = None,
    data: dict[str, Any] | None = None,
) -> str:
    timestamp = _utc_now_iso()
    evidence = AgentEvidence(
        evidence_id=_protocol_id("evidence", kind, stage, layer, title, status, uri, data),
        kind=kind,
        stage=stage,
        layer=layer,
        title=_text(title, limit=180),
        status=status,
        summary=_text(summary, limit=600),
        uri=uri,
        data=redact_sensitive_data(data or {}),
        timestamp=timestamp,
    )
    payload = _append_protocol_record(
        state,
        "agent_evidence",
        evidence.model_dump(mode="json", exclude_none=True),
    )
    return str(payload["evidence_id"])


def append_agent_observation(
    state: dict[str, Any],
    *,
    stage: str,
    layer: str,
    tool_name: str,
    status: str,
    summary: str,
    outcome: str | None = None,
    action_id: str | None = None,
    failure_type: str | None = None,
    inputs: dict[str, Any] | None = None,
    outputs: dict[str, Any] | None = None,
    evidence_ids: list[str] | None = None,
    tool_call_ids: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    timestamp = _utc_now_iso()
    observation = AgentObservation(
        observation_id=_protocol_id(
            "observation",
            stage,
            layer,
            tool_name,
            status,
            summary,
            failure_type,
            inputs,
            outputs,
            timestamp,
        ),
        stage=stage,
        layer=layer,
        tool_name=tool_name,
        status=status,
        outcome=outcome or _outcome_from_status(status, failure_type),
        summary=_text(summary, limit=600),
        action_id=action_id,
        failure_type=failure_type,
        inputs=redact_sensitive_data(inputs or {}),
        outputs=redact_sensitive_data(outputs or {}),
        evidence_ids=evidence_ids or [],
        tool_call_ids=tool_call_ids or [],
        metadata=redact_sensitive_data(metadata or {}),
        timestamp=timestamp,
    )
    payload = _append_protocol_record(
        state,
        "agent_observations",
        observation.model_dump(mode="json", exclude_none=True),
    )
    _protocol_summary(state)
    return payload


def append_api_result_observations(
    state: dict[str, Any],
    api_result: dict[str, Any] | None = None,
    *,
    stage: str = "api_runner",
) -> list[dict[str, Any]]:
    result = api_result or state.get("api_execution_result") or {}
    observations: list[dict[str, Any]] = []
    for index, item in enumerate(result.get("results") or []):
        if not isinstance(item, dict):
            continue
        skipped = bool(item.get("skipped"))
        passed = item.get("passed")
        failure_type = _api_failure_type(item)
        if skipped:
            status = "skipped"
        elif passed is True:
            status = "success"
        elif passed is None and not failure_type:
            status = "skipped"
        else:
            status = "failed"

        method = str(item.get("method") or "GET").upper()
        url = str(item.get("url") or "")
        path = urlsplit(url).path or url
        label = str(item.get("label") or f"{method} {path}")
        tool_call_id = _append_tool_call_protocol(
            state,
            tool_name="api.http_request" if item.get("http_executed") else "api.execution_gate",
            layer="api",
            status=status,
            inputs={
                "method": method,
                "url": url,
                "path": path,
                "category": item.get("category"),
                "request_headers": item.get("request_headers"),
                "request_body": item.get("request_body"),
            },
            outputs={
                "status_code": item.get("status_code"),
                "envelope_status_code": item.get("envelope_status_code"),
                "passed": item.get("passed"),
                "skipped": skipped,
                "skip_reason": item.get("skip_reason"),
                "failure_type": failure_type,
            },
            elapsed_ms=float(item.get("elapsed_ms") or 0),
        )
        response_evidence_id = _append_evidence_protocol(
            state,
            kind="api_response",
            stage=stage,
            layer="api",
            title=label,
            status=status,
            summary=(
                f"{method} {path} -> {item.get('status_code')}"
                if item.get("status_code") is not None
                else str(item.get("skip_reason") or label)
            ),
            data={
                "method": method,
                "url": url,
                "status_code": item.get("status_code"),
                "elapsed_ms": item.get("elapsed_ms"),
                "body": _compact_jsonable(item.get("body")),
                "assertion_results": item.get("assertion_results") or [],
                "failure_reason": item.get("failure_reason") or item.get("error"),
                "skip_type": item.get("skip_type"),
                "skip_reason": item.get("skip_reason"),
            },
        )
        evidence_ids = [response_evidence_id]
        for assertion_index, assertion in enumerate(item.get("assertion_results") or []):
            if not isinstance(assertion, dict):
                continue
            assertion_status = (
                "skipped"
                if assertion.get("skipped")
                else "success"
                if assertion.get("passed") in {True, None}
                else "failed"
            )
            evidence_ids.append(
                _append_evidence_protocol(
                    state,
                    kind="api_assertion",
                    stage=stage,
                    layer="api",
                    title=f"{label} assertion {assertion_index + 1}",
                    status=assertion_status,
                    summary=str(assertion.get("error") or assertion.get("type") or "assertion"),
                    data=assertion,
                )
            )

        observations.append(
            append_agent_observation(
                state,
                stage=stage,
                layer="api",
                tool_name="api.http_request",
                status=status,
                outcome=_outcome_from_status(status, failure_type),
                summary=(
                    f"{label}: {status}"
                    + (f" ({failure_type})" if failure_type else "")
                ),
                failure_type=failure_type,
                inputs={
                    "method": method,
                    "url": url,
                    "path": path,
                    "category": item.get("category"),
                    "request_body_source": item.get("request_body_source"),
                },
                outputs={
                    "status_code": item.get("status_code"),
                    "envelope_status_code": item.get("envelope_status_code"),
                    "passed": item.get("passed"),
                    "skipped": skipped,
                    "skip_reason": item.get("skip_reason"),
                    "assertion_count": len(item.get("assertion_results") or []),
                    "failure_reason": item.get("failure_reason") or item.get("error"),
                },
                evidence_ids=evidence_ids,
                tool_call_ids=[tool_call_id],
                metadata={"source": "api_execution_result", "result_index": index},
            )
        )
    return observations


def append_ui_result_observations(
    state: dict[str, Any],
    ui_result: dict[str, Any] | None = None,
    *,
    stage: str = "ui_runner",
) -> list[dict[str, Any]]:
    result = ui_result or state.get("ui_execution_result") or {}
    observations: list[dict[str, Any]] = []
    for index, item in enumerate(result.get("commands") or []):
        if not isinstance(item, dict):
            continue
        failure_type = _ui_failure_type(item)
        if item.get("status") == "blocked":
            status = "blocked"
        elif item.get("status") == "skipped":
            status = "skipped"
        elif item.get("passed") is True and int(item.get("status_code") or 0) == 0:
            status = "success"
        else:
            status = "failed"
        command = str(item.get("normalized_command") or item.get("command") or "")
        source_command = str(item.get("command") or command)
        tool_call_id = _append_tool_call_protocol(
            state,
            tool_name="ui.playwright_cli",
            layer="ui",
            status=status,
            inputs={
                "command": command,
                "source_command": source_command,
                "case_index": item.get("case_index"),
                "case_title": item.get("case_title"),
                "agent_action_type": item.get("agent_action_type"),
                "transport": item.get("transport"),
                "risk": item.get("risk"),
            },
            outputs={
                "status_code": item.get("status_code"),
                "passed": item.get("passed"),
                "stderr": _text(item.get("stderr"), limit=300),
                "stdout_chars": len(str(item.get("stdout") or "")),
                "failure_type": failure_type,
            },
            case_index=item.get("case_index"),
            case_title=item.get("case_title"),
        )
        evidence_ids: list[str] = []
        if item.get("screenshot"):
            evidence_ids.append(
                _append_evidence_protocol(
                    state,
                    kind="ui_screenshot",
                    stage=stage,
                    layer="ui",
                    title=str(item.get("evidence_label") or item.get("case_title") or "UI screenshot"),
                    status=status,
                    summary=str(item.get("evidence_detail") or source_command),
                    uri=str(item.get("screenshot")),
                    data=item.get("screenshot_evidence") or {},
                )
            )
        if item.get("stdout") and str(item.get("normalized_command") or item.get("command") or "").startswith("snapshot"):
            evidence_ids.append(
                _append_evidence_protocol(
                    state,
                    kind="ui_snapshot",
                    stage=stage,
                    layer="ui",
                    title=str(item.get("case_title") or "UI snapshot"),
                    status=status,
                    summary=_text(item.get("stdout"), limit=500),
                    data={"snapshot_text": _text(item.get("stdout"), limit=2000)},
                )
            )
        if item.get("assertion"):
            evidence_ids.append(
                _append_evidence_protocol(
                    state,
                    kind="ui_assertion",
                    stage=stage,
                    layer="ui",
                    title=f"{item.get('case_title') or 'UI case'} assertion",
                    status=status,
                    summary=str((item.get("assertion") or {}).get("expected") or "UI assertion"),
                    data=item.get("assertion") or {},
                )
            )
        observations.append(
            append_agent_observation(
                state,
                stage=stage,
                layer="ui",
                tool_name="ui.playwright_cli",
                status=status,
                outcome=_outcome_from_status(status, failure_type),
                summary=(
                    f"{item.get('case_title') or 'UI command'}: {source_command} -> {status}"
                    + (f" ({failure_type})" if failure_type else "")
                ),
                failure_type=failure_type,
                inputs={
                    "command": command,
                    "source_command": source_command,
                    "case_index": item.get("case_index"),
                    "case_title": item.get("case_title"),
                    "agent_action_type": item.get("agent_action_type"),
                    "transport": item.get("transport"),
                    "risk": item.get("risk"),
                },
                outputs={
                    "status_code": item.get("status_code"),
                    "passed": item.get("passed"),
                    "stderr": _text(item.get("stderr"), limit=500),
                    "normalization": item.get("normalization"),
                    "agent_action_type": item.get("agent_action_type"),
                    "transport": item.get("transport"),
                    "risk": item.get("risk"),
                },
                evidence_ids=evidence_ids,
                tool_call_ids=[tool_call_id],
                metadata={
                    "source": "ui_execution_result",
                    "result_index": index,
                    "agent_action": item.get("agent_action"),
                    "agent_action_type": item.get("agent_action_type"),
                    "transport": item.get("transport"),
                    "risk": item.get("risk"),
                },
            )
        )
    return observations


def append_evaluation_protocol(
    state: dict[str, Any],
    evaluation: dict[str, Any],
    *,
    stage: str | None = None,
) -> dict[str, Any]:
    resolved_stage = str(stage or evaluation.get("stage") or state.get("agent_execution_stage") or "agent")
    observations = [
        item
        for item in state.get("agent_observations") or []
        if isinstance(item, dict) and item.get("stage") in {resolved_stage, f"{resolved_stage}_runner"}
    ]
    failure_types = [str(item.get("failure_type")) for item in observations if item.get("failure_type")]
    failure_type = str(evaluation.get("failure_type") or (failure_types[0] if failure_types else "")) or None
    next_action = str(evaluation.get("next_action") or "report")
    sufficient = bool(evaluation.get("sufficient_evidence"))
    outcome = (
        "needs_replan"
        if next_action.startswith("replan")
        else "needs_human"
        if next_action == "ask_human"
        else "sufficient"
        if sufficient
        else "insufficient"
    )
    protocol = AgentEvaluation(
        evaluation_id=_protocol_id(
            "evaluation",
            resolved_stage,
            next_action,
            sufficient,
            evaluation.get("reason"),
            len(state.get("agent_protocol_evaluations") or []),
        ),
        stage=resolved_stage,
        sufficient_evidence=sufficient,
        outcome=outcome,
        next_action=next_action,
        confidence=str(evaluation.get("confidence") or "unknown"),
        reason=_text(evaluation.get("reason"), limit=800),
        failure_type=failure_type,
        missing_evidence=[
            _text(item, limit=240)
            for item in _as_list(evaluation.get("missing_evidence"))
            if _text(item, limit=240)
        ],
        replan_hint=_text(
            evaluation.get("replan_instructions") or evaluation.get("replan_hint"),
            limit=800,
        ),
        observation_ids=[
            str(item.get("observation_id"))
            for item in observations[-40:]
            if item.get("observation_id")
        ],
        timestamp=_utc_now_iso(),
    )
    payload = _append_protocol_record(
        state,
        "agent_protocol_evaluations",
        protocol.model_dump(mode="json", exclude_none=True),
    )
    _protocol_summary(state)
    return payload


def validate_and_record_agent_action_plan(
    state: dict[str, Any],
    *,
    stage: str,
    strategy: dict[str, Any] | None,
    parsed_api_schema: list[dict[str, Any]] | None,
    execution_policy: str,
) -> list[dict[str, Any]]:
    actions = validate_agent_action_plan(
        (strategy or {}).get("tool_plan") or state.get("agent_tool_plan") or [],
        strategy=strategy,
        parsed_api_schema=parsed_api_schema,
        execution_policy=execution_policy,
    )
    state["agent_actions"] = actions
    diagnostics: list[dict[str, Any]] = []
    for action in actions:
        diagnostics.extend(_as_list(action.get("diagnostics")))
        record_agent_action_observation(state, action, stage=stage)
    if diagnostics:
        state["agent_action_diagnostics"] = redact_sensitive_data(diagnostics)
    return actions


def find_agent_action(
    actions: list[dict[str, Any]] | None,
    tool_name: str,
) -> dict[str, Any] | None:
    for action in actions or []:
        if isinstance(action, dict) and action.get("tool_name") == tool_name:
            return action
    return None

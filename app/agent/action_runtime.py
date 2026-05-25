from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

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
AGENT_ACTION_CONTRACT_SOURCE = "agent_action_contract"
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


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any, *, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


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

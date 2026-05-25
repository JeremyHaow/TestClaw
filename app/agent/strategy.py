from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.agent.api_scope import (
    ALL_SAFE_GET_COVERAGE_GOAL,
    ALL_SAFE_GET_COVERAGE_SOURCE,
    SAFE_API_METHODS,
    WRITE_API_METHODS,
    _normalize_method,
    _normalize_path,
    _path_template_re,
    objective_requests_all_safe_get_coverage,
    safe_schema_method_endpoints,
)
from app.agent.tool_registry import allowed_tool_names

AGENT_STRATEGY_SCHEMA_VERSION = "2026-05-25"
STRATEGY_SCHEMA_SOURCE = "agent_strategy_schema"
STRATEGY_FALLBACK_SOURCE = "agent_strategy_fallback"

KNOWN_STRATEGY_INTENTS = {
    "api_contract",
    "api_read_only_coverage",
    "api_focused_endpoints",
    "ui_exploration",
    "full_flow",
    "blocked",
}
KNOWN_COVERAGE_SCOPES = {
    "all_documented_safe_methods",
    "focused_documented_endpoints",
    "sampled_contract",
    "ui_paths",
    "none",
}
KNOWN_ENDPOINT_SOURCES = {
    "schema",
    "suite",
    "memory",
    "model_focus",
    "fallback",
}
KNOWN_BUDGET_BEHAVIORS = {
    "cover_all_within_budget",
    "sample_representative",
    "focused_only",
}
KNOWN_CONFIDENCE = {"low", "medium", "high"}
KNOWN_TOOL_NAMES = allowed_tool_names()


class StrategyDiagnostic(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: str
    severity: str = "warning"
    action: str = "recorded"
    detail: str
    method: str | None = None
    path: str | None = None
    tool_name: str | None = None


class EndpointReference(BaseModel):
    model_config = ConfigDict(extra="ignore")

    method: str
    path: str


class MethodPolicy(BaseModel):
    model_config = ConfigDict(extra="ignore")

    allowed_methods: list[str] = Field(default_factory=list)
    blocked_methods: list[str] = Field(default_factory=list)
    write_allowed: bool = False


class EndpointSelection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source: str = "fallback"
    include: list[EndpointReference] = Field(default_factory=list)
    exclude: list[EndpointReference] = Field(default_factory=list)
    budget_behavior: str = "sample_representative"


class ToolPlanStep(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tool_name: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    safety_constraints: list[str] = Field(default_factory=list)
    expected_observation: str = ""
    reason: str = ""


class AgentStrategyDecision(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: str = AGENT_STRATEGY_SCHEMA_VERSION
    intent: str = "blocked"
    coverage_scope: str = "none"
    method_policy: MethodPolicy = Field(default_factory=MethodPolicy)
    endpoint_selection: EndpointSelection = Field(default_factory=EndpointSelection)
    tool_plan: list[ToolPlanStep] = Field(default_factory=list)
    case_generation_guidance: str = ""
    success_criteria: list[str] = Field(default_factory=list)
    confidence: str = "medium"
    reason: str = ""
    diagnostics: list[StrategyDiagnostic] = Field(default_factory=list)
    source: str = "llm"
    valid: bool = True
    schema_endpoint_count: int = 0
    selected_endpoint_count: int = 0
    fallback_reason: str | None = None


def _diagnostic(
    kind: str,
    detail: str,
    *,
    severity: str = "warning",
    action: str = "recorded",
    method: str | None = None,
    path: str | None = None,
    tool_name: str | None = None,
) -> StrategyDiagnostic:
    return StrategyDiagnostic(
        kind=kind,
        severity=severity,
        action=action,
        detail=detail,
        method=method,
        path=path,
        tool_name=tool_name,
    )


def _text(value: Any, *, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _lower_choice(value: Any, known: set[str], default: str, diagnostics: list[StrategyDiagnostic], kind: str) -> str:
    text = str(value or "").strip().lower()
    if text in known:
        return text
    if text:
        diagnostics.append(
            _diagnostic(
                kind=kind,
                detail=f"Unknown value '{text}' was replaced with '{default}'.",
                action="normalized",
            )
        )
    return default


def _policy_methods(execution_policy: str) -> tuple[list[str], list[str], bool]:
    policy = str(execution_policy or "safe_read_only").strip().lower()
    if policy == "write_allowed":
        allowed = sorted(SAFE_API_METHODS | WRITE_API_METHODS)
        return allowed, [], True
    return sorted(SAFE_API_METHODS), sorted(WRITE_API_METHODS), False


def _normalize_method_policy(
    raw: Any,
    *,
    execution_policy: str,
    diagnostics: list[StrategyDiagnostic],
) -> MethodPolicy:
    policy_allowed, policy_blocked, write_allowed = _policy_methods(execution_policy)
    raw_policy = raw if isinstance(raw, dict) else {}
    requested = [
        _normalize_method(item)
        for item in _as_list(raw_policy.get("allowed_methods"))
        if _normalize_method(item)
    ]
    allowed = sorted({method for method in (requested or policy_allowed) if method in policy_allowed})
    dropped = sorted({method for method in requested if method not in policy_allowed})
    for method in dropped:
        diagnostics.append(
            _diagnostic(
                kind="method_blocked_by_policy",
                detail=f"Model requested {method}, but execution policy '{execution_policy}' does not allow it.",
                action="dropped",
                method=method,
            )
        )
    if not allowed:
        allowed = policy_allowed

    requested_write_allowed = bool(raw_policy.get("write_allowed"))
    if requested_write_allowed and not write_allowed:
        diagnostics.append(
            _diagnostic(
                kind="write_policy_overridden",
                detail="Model requested write_allowed=true, but local execution policy is read-only.",
                action="forced_false",
            )
        )

    blocked = sorted(set(policy_blocked) | {method for method in WRITE_API_METHODS if method not in allowed})
    return MethodPolicy(
        allowed_methods=allowed,
        blocked_methods=blocked,
        write_allowed=write_allowed,
    )


def _endpoint_key(method: Any, path: Any) -> tuple[str, str]:
    return _normalize_method(method), _normalize_path(path)


def _schema_endpoint_match(
    method: str,
    path: str,
    schema_endpoints: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not method or not path:
        return None
    for endpoint in schema_endpoints:
        endpoint_method, endpoint_path = _endpoint_key(endpoint.get("method"), endpoint.get("path"))
        if endpoint_method != method or not endpoint_path:
            continue
        if path == endpoint_path or _path_template_re(endpoint_path).match(path):
            return endpoint
    return None


def _normalize_endpoint_refs(
    raw_items: Any,
    *,
    schema_endpoints: list[dict[str, Any]],
    method_policy: MethodPolicy,
    diagnostics: list[StrategyDiagnostic],
    action: str,
) -> list[EndpointReference]:
    refs: list[EndpointReference] = []
    seen: set[tuple[str, str]] = set()
    allowed_methods = set(method_policy.allowed_methods)
    schema_backed = bool(schema_endpoints)
    for item in _as_list(raw_items):
        if not isinstance(item, dict):
            diagnostics.append(
                _diagnostic(
                    kind="invalid_endpoint_reference",
                    detail="Endpoint reference was not an object and was ignored.",
                    action="dropped",
                )
            )
            continue
        method, path = _endpoint_key(item.get("method"), item.get("path"))
        if not method or not path:
            diagnostics.append(
                _diagnostic(
                    kind="invalid_endpoint_reference",
                    detail="Endpoint reference must contain method and path.",
                    action="dropped",
                    method=method or None,
                    path=path or None,
                )
            )
            continue
        if method not in allowed_methods:
            diagnostics.append(
                _diagnostic(
                    kind="method_blocked_by_policy",
                    detail=f"{method} {path} is not allowed by local method policy.",
                    action="dropped",
                    method=method,
                    path=path,
                )
            )
            continue
        endpoint = _schema_endpoint_match(method, path, schema_endpoints)
        if schema_backed and endpoint is None:
            diagnostics.append(
                _diagnostic(
                    kind="out_of_schema_endpoint",
                    detail=f"{method} {path} is not present in the loaded OpenAPI schema.",
                    action="dropped",
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
        refs.append(EndpointReference(method=method, path=canonical_path))
    if raw_items and not refs and action == "include":
        diagnostics.append(
            _diagnostic(
                kind="empty_valid_endpoint_selection",
                detail="No model-selected endpoint survived local safety and schema validation.",
                action="recorded",
            )
        )
    return refs


def _refs_from_schema(endpoints: list[dict[str, Any]], method_policy: MethodPolicy) -> list[EndpointReference]:
    allowed = set(method_policy.allowed_methods)
    refs: list[EndpointReference] = []
    seen: set[tuple[str, str]] = set()
    for endpoint in endpoints:
        method, path = _endpoint_key(endpoint.get("method"), endpoint.get("path"))
        if not method or not path or method not in allowed:
            continue
        key = (method, path)
        if key in seen:
            continue
        seen.add(key)
        refs.append(EndpointReference(method=method, path=path))
    return refs


def _normalize_endpoint_selection(
    raw: Any,
    *,
    coverage_scope: str,
    schema_endpoints: list[dict[str, Any]],
    method_policy: MethodPolicy,
    diagnostics: list[StrategyDiagnostic],
) -> EndpointSelection:
    raw_selection = raw if isinstance(raw, dict) else {}
    source = _lower_choice(
        raw_selection.get("source"),
        KNOWN_ENDPOINT_SOURCES,
        "fallback",
        diagnostics,
        "unknown_endpoint_source",
    )
    budget_behavior = _lower_choice(
        raw_selection.get("budget_behavior"),
        KNOWN_BUDGET_BEHAVIORS,
        "sample_representative",
        diagnostics,
        "unknown_budget_behavior",
    )
    include = _normalize_endpoint_refs(
        raw_selection.get("include"),
        schema_endpoints=schema_endpoints,
        method_policy=method_policy,
        diagnostics=diagnostics,
        action="include",
    )
    exclude = _normalize_endpoint_refs(
        raw_selection.get("exclude"),
        schema_endpoints=schema_endpoints,
        method_policy=method_policy,
        diagnostics=diagnostics,
        action="exclude",
    )

    if coverage_scope == "all_documented_safe_methods":
        include = _refs_from_schema(safe_schema_method_endpoints(schema_endpoints), method_policy)
        excluded = {(ref.method, ref.path) for ref in exclude}
        include = [ref for ref in include if (ref.method, ref.path) not in excluded]
        budget_behavior = "cover_all_within_budget"
        source = "schema"
    elif coverage_scope in {"focused_documented_endpoints", "sampled_contract"} and not include:
        diagnostics.append(
            _diagnostic(
                kind="missing_strategy_endpoint_include",
                detail=f"Coverage scope '{coverage_scope}' requires validated documented endpoints.",
                action="recorded",
            )
        )

    return EndpointSelection(
        source=source,
        include=include,
        exclude=exclude,
        budget_behavior=budget_behavior,
    )


def _normalize_tool_plan(
    raw: Any,
    *,
    coverage_scope: str,
    diagnostics: list[StrategyDiagnostic],
) -> list[ToolPlanStep]:
    steps: list[ToolPlanStep] = []
    has_schema_request_step = False
    for item in _as_list(raw):
        if not isinstance(item, dict):
            diagnostics.append(
                _diagnostic(
                    kind="invalid_tool_plan_step",
                    detail="Tool plan step was not an object and was ignored.",
                    action="dropped",
                )
            )
            continue
        tool_name = str(item.get("tool_name") or "").strip()
        if tool_name not in KNOWN_TOOL_NAMES:
            diagnostics.append(
                _diagnostic(
                    kind="unknown_tool_name",
                    detail=f"Unknown tool '{tool_name}' will be blocked by the action runtime.",
                    action="blocked",
                    tool_name=tool_name or None,
                )
            )
            if not tool_name:
                continue
        elif tool_name == "api.derive_schema_requests":
            has_schema_request_step = True
        inputs = item.get("inputs")
        constraints = [
            _text(value, limit=120)
            for value in _as_list(item.get("safety_constraints"))
            if _text(value, limit=120)
        ]
        steps.append(
            ToolPlanStep(
                tool_name=tool_name,
                inputs=inputs if isinstance(inputs, dict) else {},
                safety_constraints=constraints,
                expected_observation=_text(item.get("expected_observation"), limit=240),
                reason=_text(item.get("reason"), limit=360),
            )
        )

    if coverage_scope in {
        "all_documented_safe_methods",
        "focused_documented_endpoints",
        "sampled_contract",
    }:
        if not has_schema_request_step:
            steps.append(
                ToolPlanStep(
                    tool_name="api.derive_schema_requests",
                    inputs={"scope": coverage_scope},
                    safety_constraints=["schema_only", "local_method_policy"],
                    expected_observation="selected request count, skipped count, and guardrail diagnostics",
                )
            )
    elif not steps:
        if coverage_scope == "ui_paths":
            steps.append(
                ToolPlanStep(
                    tool_name="ui.playwright_cli",
                    inputs={"scope": "ui_paths"},
                    safety_constraints=["browser_only", "redact_sensitive_inputs"],
                    expected_observation="snapshot, screenshot, and command result evidence",
                )
            )
        else:
            steps.append(
                ToolPlanStep(
                    tool_name="planner.generate_test_cases",
                    inputs={"scope": coverage_scope},
                    safety_constraints=["schema_only_when_schema_loaded", "local_method_policy"],
                    expected_observation="validated API/UI case count and diagnostics",
                )
            )
    return steps


def normalize_agent_strategy_decision(
    raw: Any,
    *,
    parsed_api_schema: list[dict[str, Any]] | None,
    execution_policy: str = "safe_read_only",
    test_type: str = "auto",
    source: str = "llm",
    fallback_reason: str | None = None,
) -> dict[str, Any]:
    diagnostics: list[StrategyDiagnostic] = []
    if not isinstance(raw, dict):
        diagnostics.append(
            _diagnostic(
                kind="invalid_strategy_json",
                detail="Planner strategy output was not a JSON object.",
                severity="error",
                action="blocked",
            )
        )
        raw = {}

    test_mode = str(test_type or "auto").strip().lower()
    default_intent = "ui_exploration" if test_mode == "ui" else "api_contract"
    intent = _lower_choice(
        raw.get("intent"),
        KNOWN_STRATEGY_INTENTS,
        default_intent,
        diagnostics,
        "unknown_strategy_intent",
    )
    coverage_scope = _lower_choice(
        raw.get("coverage_scope"),
        KNOWN_COVERAGE_SCOPES,
        "none",
        diagnostics,
        "unknown_coverage_scope",
    )
    if test_mode == "ui" and coverage_scope not in {"ui_paths", "none"}:
        diagnostics.append(
            _diagnostic(
                kind="strategy_mode_mismatch",
                detail="API coverage scope was removed because this is a UI-only run.",
                action="normalized",
            )
        )
        coverage_scope = "ui_paths"
        intent = "ui_exploration"

    schema_endpoints = [
        endpoint for endpoint in (parsed_api_schema or []) if isinstance(endpoint, dict)
    ]
    method_policy = _normalize_method_policy(
        raw.get("method_policy"),
        execution_policy=execution_policy,
        diagnostics=diagnostics,
    )
    endpoint_selection = _normalize_endpoint_selection(
        raw.get("endpoint_selection"),
        coverage_scope=coverage_scope,
        schema_endpoints=schema_endpoints,
        method_policy=method_policy,
        diagnostics=diagnostics,
    )
    tool_plan = _normalize_tool_plan(
        raw.get("tool_plan"),
        coverage_scope=coverage_scope,
        diagnostics=diagnostics,
    )
    confidence = _lower_choice(
        raw.get("confidence"),
        KNOWN_CONFIDENCE,
        "medium",
        diagnostics,
        "unknown_strategy_confidence",
    )

    reason = _text(raw.get("reason"), limit=500)
    if not reason:
        reason = "Strategy selected from objective, schema, auth state, memory, and safety policy."

    success_criteria = [
        _text(item, limit=240) for item in _as_list(raw.get("success_criteria")) if _text(item, limit=240)
    ]
    if not success_criteria:
        success_criteria = [
            "Every selected tool action produces evidence or an explicit guardrail diagnostic."
        ]

    for item in _as_list(raw.get("diagnostics")):
        if isinstance(item, dict):
            diagnostics.append(
                _diagnostic(
                    kind=_text(item.get("kind") or "model_diagnostic", limit=80),
                    severity=_text(item.get("severity") or "info", limit=40),
                    action=_text(item.get("action") or "recorded", limit=80),
                    detail=_text(item.get("detail") or item.get("reason") or item, limit=300),
                    method=_normalize_method(item.get("method")) if item.get("method") else None,
                    path=_normalize_path(item.get("path")) if item.get("path") else None,
                    tool_name=_text(item.get("tool_name"), limit=120) or None,
                )
            )
        else:
            diagnostics.append(
                _diagnostic(
                    kind="model_diagnostic",
                    severity="info",
                    detail=_text(item, limit=300),
                )
            )

    fatal = any(diag.severity == "error" and diag.action == "blocked" for diag in diagnostics)
    decision = AgentStrategyDecision(
        intent=intent,
        coverage_scope=coverage_scope,
        method_policy=method_policy,
        endpoint_selection=endpoint_selection,
        tool_plan=tool_plan,
        case_generation_guidance=_text(raw.get("case_generation_guidance"), limit=900),
        success_criteria=success_criteria,
        confidence=confidence,
        reason=reason,
        diagnostics=diagnostics,
        source=source,
        valid=not fatal,
        schema_endpoint_count=len(schema_endpoints),
        selected_endpoint_count=len(endpoint_selection.include),
        fallback_reason=fallback_reason,
    )
    return decision.model_dump(mode="json")


def fallback_agent_strategy_decision(
    *,
    objective: Any,
    parsed_api_schema: list[dict[str, Any]] | None,
    execution_policy: str = "safe_read_only",
    test_type: str = "auto",
    reason: str = "Planner model was unavailable or returned invalid strategy JSON.",
) -> dict[str, Any]:
    test_mode = str(test_type or "auto").strip().lower()
    safe_endpoints = safe_schema_method_endpoints(parsed_api_schema)
    if test_mode == "ui":
        raw = {
            "intent": "ui_exploration",
            "coverage_scope": "ui_paths",
            "endpoint_selection": {"source": "fallback", "include": [], "budget_behavior": "focused_only"},
            "reason": reason,
            "confidence": "medium",
        }
    elif objective_requests_all_safe_get_coverage(objective) and safe_endpoints:
        raw = {
            "intent": "api_read_only_coverage",
            "coverage_scope": "all_documented_safe_methods",
            "method_policy": {"allowed_methods": sorted(SAFE_API_METHODS), "write_allowed": False},
            "endpoint_selection": {
                "source": "schema",
                "include": [],
                "exclude": [],
                "budget_behavior": "cover_all_within_budget",
            },
            "tool_plan": [
                {
                    "tool_name": "api.derive_schema_requests",
                    "inputs": {"scope": "all_documented_safe_methods"},
                    "safety_constraints": ["schema_only", "safe_methods_only"],
                    "expected_observation": "selected safe request count and budget omissions",
                }
            ],
            "case_generation_guidance": "Derive safe requests from the documented schema; keep uncertain assertions advisory.",
            "success_criteria": [
                "Every documented safe endpoint has request evidence or a budget/guardrail diagnostic."
            ],
            "reason": reason,
            "confidence": "medium",
        }
    elif parsed_api_schema:
        raw = {
            "intent": "api_contract",
            "coverage_scope": "none",
            "endpoint_selection": {"source": "fallback", "include": [], "budget_behavior": "sample_representative"},
            "reason": reason,
            "confidence": "low",
        }
    else:
        raw = {
            "intent": "blocked",
            "coverage_scope": "none",
            "endpoint_selection": {"source": "fallback", "include": [], "budget_behavior": "focused_only"},
            "reason": reason,
            "confidence": "low",
        }

    return normalize_agent_strategy_decision(
        raw,
        parsed_api_schema=parsed_api_schema,
        execution_policy=execution_policy,
        test_type=test_type,
        source=STRATEGY_FALLBACK_SOURCE,
        fallback_reason=reason,
    )


def strategy_requests_all_safe_coverage(strategy: dict[str, Any] | None) -> bool:
    if not isinstance(strategy, dict) or not strategy.get("valid", True):
        return False
    return (
        strategy.get("coverage_scope") == "all_documented_safe_methods"
        and strategy.get("intent") in {"api_read_only_coverage", "api_contract", "full_flow"}
    )


def strategy_requests_schema_endpoint_selection(strategy: dict[str, Any] | None) -> bool:
    if not isinstance(strategy, dict) or not strategy.get("valid", True):
        return False
    return strategy.get("coverage_scope") in {
        "focused_documented_endpoints",
        "sampled_contract",
    }


def strategy_selected_schema_endpoints(
    strategy: dict[str, Any] | None,
    parsed_api_schema: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    if not isinstance(strategy, dict):
        return []
    selection = strategy.get("endpoint_selection") if isinstance(strategy.get("endpoint_selection"), dict) else {}
    refs = selection.get("include") if isinstance(selection, dict) else []
    schema_endpoints = [
        endpoint for endpoint in (parsed_api_schema or []) if isinstance(endpoint, dict)
    ]
    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for ref in _as_list(refs):
        if not isinstance(ref, dict):
            continue
        method, path = _endpoint_key(ref.get("method"), ref.get("path"))
        endpoint = _schema_endpoint_match(method, path, schema_endpoints)
        if endpoint is None:
            continue
        key = _endpoint_key(endpoint.get("method"), endpoint.get("path"))
        if key in seen:
            continue
        seen.add(key)
        selected.append(endpoint)
    return selected


def strategy_coverage_goal(strategy: dict[str, Any] | None) -> str | None:
    if strategy_requests_all_safe_coverage(strategy):
        return ALL_SAFE_GET_COVERAGE_GOAL
    if strategy_requests_schema_endpoint_selection(strategy):
        return str(strategy.get("coverage_scope"))
    return None


def strategy_request_source(strategy: dict[str, Any] | None) -> str | None:
    if strategy_requests_all_safe_coverage(strategy):
        return ALL_SAFE_GET_COVERAGE_SOURCE
    if strategy_requests_schema_endpoint_selection(strategy):
        return STRATEGY_SCHEMA_SOURCE
    return None


def strategy_summary(strategy: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(strategy, dict):
        return {}
    return {
        "intent": strategy.get("intent"),
        "coverage_scope": strategy.get("coverage_scope"),
        "source": strategy.get("source"),
        "confidence": strategy.get("confidence"),
        "selected_endpoint_count": strategy.get("selected_endpoint_count"),
        "diagnostic_count": len(strategy.get("diagnostics") or []),
    }

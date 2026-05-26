from __future__ import annotations

from typing import Any

from app.agent.action_runtime import (
    record_agent_action_observation,
    validate_agent_action_plan,
)
from app.agent.api_scope import safe_schema_method_endpoints
from app.agent.progress import persist_progress
from app.agent.state import AgentState
from app.agent.tool_registry import install_tool_context, record_tool_call
from app.services.api_auth import coerce_auth_config, login_endpoint_for_config
from app.services.auth_preflight_service import api_validation_candidates


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _action_names(actions: list[dict[str, Any]]) -> set[str]:
    return {str(action.get("tool_name") or "") for action in actions if isinstance(action, dict)}


def _credential_fields(state: AgentState) -> list[str]:
    fields: list[str] = []
    credentials = state.get("auth_credentials") if isinstance(state.get("auth_credentials"), dict) else {}
    config = coerce_auth_config(state.get("auth_config"))
    for key in ("username", "password", "captcha", "csrf", "tenant"):
        if credentials.get(key) or config.get(key):
            fields.append(key)
    body = config.get("body")
    if isinstance(body, dict):
        fields.extend(str(key) for key in body)
    return sorted(set(fields))


def _needs_auth_discovery(state: AgentState) -> bool:
    if not state.get("parsed_api_schema"):
        return False
    if _credential_fields(state):
        return True
    auth_preflight = state.get("auth_preflight") if isinstance(state.get("auth_preflight"), dict) else {}
    if auth_preflight:
        return True
    return any(
        isinstance(endpoint, dict) and endpoint.get("auth_required")
        for endpoint in (state.get("parsed_api_schema") or [])
    )


def _extra_tool_steps(state: AgentState, existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    names = _action_names(existing)
    steps: list[dict[str, Any]] = []
    if _needs_auth_discovery(state) and "auth.discover_candidates" not in names:
        steps.append(
            {
                "tool_name": "auth.discover_candidates",
                "inputs": {
                    "target_url": state.get("target_url") or state.get("base_url_override") or "",
                    "credential_fields": _credential_fields(state),
                },
                "safety_constraints": ["openapi_only", "redact_credentials", "readonly_validation_only"],
                "expected_observation": "login candidate and protected read-only validation candidate counts",
                "reason": "Credentials or security schemes are present, so discover auth routes before execution.",
                "source": "deterministic_supervisor",
            }
        )

    auth_preflight = state.get("auth_preflight") if isinstance(state.get("auth_preflight"), dict) else {}
    if (
        str(auth_preflight.get("status") or "").lower() == "blocked"
        and "human.ask" not in names
    ):
        steps.append(
            {
                "tool_name": "human.ask",
                "inputs": {
                    "missing_fields": auth_preflight.get("missing_fields") or [],
                    "reason": auth_preflight.get("next_action") or "Auth preflight is blocked.",
                },
                "safety_constraints": ["ask_smallest_missing_info", "redact_context"],
                "expected_observation": "requested missing fields and blocker reason",
                "reason": "The run is blocked and needs the smallest user-supplied auth detail.",
                "source": "deterministic_supervisor",
            }
        )

    if (
        (state.get("rag_retrieval") or state.get("rag_context") or state.get("target_memory"))
        and "memory.retrieve" not in names
    ):
        steps.append(
            {
                "tool_name": "memory.retrieve",
                "inputs": {
                    "query": state.get("objective") or "",
                    "target": state.get("target_url") or state.get("ui_seed_url") or "",
                    "limit": 5,
                },
                "safety_constraints": ["redacted_context", "bounded_sources"],
                "expected_observation": "memory source count and retrieval mode",
                "reason": "Prior run memory is available and should be visible in the tool trace.",
                "source": "deterministic_supervisor",
            }
        )
    return steps


def _selected_request_refs(action: dict[str, Any], state: AgentState) -> list[dict[str, str]]:
    inputs = action.get("inputs") if isinstance(action.get("inputs"), dict) else {}
    scope = str(inputs.get("scope") or "").lower()
    include = [
        {"method": str(item.get("method") or "").upper(), "path": str(item.get("path") or "")}
        for item in _as_list(inputs.get("include"))
        if isinstance(item, dict) and item.get("method") and item.get("path")
    ]
    exclude = {
        (str(item.get("method") or "").upper(), str(item.get("path") or ""))
        for item in _as_list(inputs.get("exclude"))
        if isinstance(item, dict)
    }
    if scope in {"focused_documented_endpoints", "sampled_contract"} and include:
        refs = include
    else:
        refs = [
            {"method": str(endpoint.get("method") or "GET").upper(), "path": str(endpoint.get("path") or "")}
            for endpoint in safe_schema_method_endpoints(state.get("parsed_api_schema") or [])
            if endpoint.get("path")
        ]
    return [ref for ref in refs if (ref["method"], ref["path"]) not in exclude]


def _observe_api_request_selection(action: dict[str, Any], state: AgentState) -> dict[str, Any]:
    refs = _selected_request_refs(action, state)
    safe_total = len(safe_schema_method_endpoints(state.get("parsed_api_schema") or []))
    selection = {
        "source": "agent_supervisor",
        "tool_name": action.get("tool_name"),
        "coverage_scope": (action.get("inputs") or {}).get("scope"),
        "selected": refs,
        "selected_total": len(refs),
        "candidate_total": safe_total,
        "policy": action.get("policy"),
    }
    state["api_request_selection"] = selection
    return {
        "selected_total": len(refs),
        "candidate_total": safe_total,
        "coverage_scope": selection["coverage_scope"],
    }


def _observe_auth_candidates(action: dict[str, Any], state: AgentState) -> dict[str, Any]:
    endpoints = [endpoint for endpoint in (state.get("parsed_api_schema") or []) if isinstance(endpoint, dict)]
    target_url = str(state.get("target_url") or state.get("base_url_override") or "")
    login_url, login_endpoint = login_endpoint_for_config(
        state.get("auth_config"),
        endpoints=endpoints,
        target_url=target_url,
    )
    validation_candidates = api_validation_candidates(
        endpoints,
        target_url,
        protected_only=True,
        limit=3,
    )
    output = {
        "login_candidate_count": 1 if login_url else 0,
        "login_path": login_endpoint.get("path") if isinstance(login_endpoint, dict) else None,
        "validation_candidate_count": len(validation_candidates),
        "credential_fields": _credential_fields(state),
        "missing_inputs": [],
    }
    if not login_url:
        output["missing_inputs"].append("login_url")
    if not validation_candidates:
        output["missing_inputs"].append("protected_read_only_endpoint")
    state["auth_discovery"] = output
    return output


def _observe_memory(state: AgentState) -> dict[str, Any]:
    retrieval = state.get("rag_retrieval") if isinstance(state.get("rag_retrieval"), dict) else {}
    sources = _as_list(retrieval.get("sources"))
    target_memory = state.get("target_memory") if isinstance(state.get("target_memory"), dict) else {}
    if not retrieval and target_memory:
        return {
            "source_count": int(target_memory.get("previous_run_count") or 0),
            "mode": "target_memory",
            "status": target_memory.get("confidence") or "observed",
        }
    return {
        "source_count": len(sources),
        "mode": retrieval.get("mode") or ("context_only" if state.get("rag_context") else "unavailable"),
        "status": retrieval.get("status") or "observed",
    }


def _observe_human(action: dict[str, Any]) -> dict[str, Any]:
    inputs = action.get("inputs") if isinstance(action.get("inputs"), dict) else {}
    fields = [str(item) for item in _as_list(inputs.get("missing_fields")) if str(item)]
    return {
        "blocking": True,
        "requested_fields": fields,
        "requested_field_count": len(fields),
        "next_action": inputs.get("reason") or "请补充缺失信息后继续。",
    }


def _execute_action(action: dict[str, Any], state: AgentState) -> tuple[str, dict[str, Any]]:
    if not action.get("allowed"):
        return "blocked", {"diagnostic_count": len(action.get("diagnostics") or [])}
    tool_name = str(action.get("tool_name") or "")
    if tool_name == "api.derive_schema_requests":
        return "success", _observe_api_request_selection(action, state)
    if tool_name == "auth.discover_candidates":
        return "success", _observe_auth_candidates(action, state)
    if tool_name == "memory.retrieve":
        return "success", _observe_memory(state)
    if tool_name == "human.ask":
        return "blocked", _observe_human(action)
    if tool_name.startswith("ui."):
        return "planned", {"expected_observation": action.get("expected_observation")}
    return "planned", {"expected_observation": action.get("expected_observation")}


async def run(state: AgentState) -> AgentState:
    install_tool_context(state)
    existing_actions = [
        action for action in (state.get("agent_actions") or []) if isinstance(action, dict)
    ]
    extra_steps = _extra_tool_steps(state, existing_actions)
    if extra_steps:
        extra_actions = validate_agent_action_plan(
            extra_steps,
            strategy=state.get("agent_strategy_decision") if isinstance(state.get("agent_strategy_decision"), dict) else None,
            parsed_api_schema=state.get("parsed_api_schema"),
            execution_policy=str(state.get("api_execution_policy") or "safe_read_only"),
        )
        existing_actions.extend(extra_actions)
        state["agent_actions"] = existing_actions

    executed = 0
    blocked = 0
    for action in existing_actions:
        status, output = _execute_action(action, state)
        if status == "success":
            executed += 1
        elif status == "blocked":
            blocked += 1
        record_agent_action_observation(
            state,
            action,
            stage="agent_supervisor",
            status=status,
            output_summary=output,
        )

    record_tool_call(
        state,
        tool_name="agent.supervisor_loop",
        layer="supervisor",
        status="success",
        input_summary={
            "action_count": len(existing_actions),
            "extra_action_count": len(extra_steps),
        },
        output_summary={
            "executed": executed,
            "blocked": blocked,
            "observed": len(existing_actions),
        },
        metadata={
            "reason": "Run a bounded supervisor prototype over validated tool actions before fixed graph execution.",
            "next_decision": "continue_existing_graph_path",
        },
    )

    detail = f"Supervisor observed {len(existing_actions)} action(s), executed {executed}, blocked {blocked}"
    state.setdefault("workflow_steps", []).append(
        {"node": "agent_supervisor", "status": "done", "detail": detail}
    )
    await persist_progress(state, "agent_supervisor", "done", detail)
    return state

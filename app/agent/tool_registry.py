from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from app.core.redaction import redact_sensitive_data


@dataclass(frozen=True)
class ToolCapability:
    name: str
    layer: str
    skill: str
    description: str
    risk: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]


@dataclass(frozen=True)
class AutomationSkill:
    name: str
    layer: str
    description: str
    triggers: list[str]
    tools: list[str]


TOOL_CAPABILITIES: tuple[ToolCapability, ...] = (
    ToolCapability(
        name="planner.parse_requirement",
        layer="planner",
        skill="test-planning",
        description="Parse natural language, URL, OpenAPI, YAML, or selected suite input into a runnable testing context.",
        risk="read_only",
        input_schema={"objective": "string", "source_input": "string", "test_type": "string"},
        output_schema={"input_type": "string", "targets": "object"},
    ),
    ToolCapability(
        name="planner.generate_execution_plan",
        layer="planner",
        skill="test-planning",
        description="Generate API/UI execution plans with priority, dependencies, and safe execution policy.",
        risk="read_only",
        input_schema={"schema": "OpenAPI endpoints", "page_snapshot": "optional string"},
        output_schema={"api_plan": "object", "ui_plan": "object"},
    ),
    ToolCapability(
        name="planner.analyze_ui_execution_context",
        layer="planner",
        skill="test-planning",
        description="Analyze UI cases against setup state and page snapshots to choose prepared-context reuse, stripping repeated setup steps, or fresh-entry execution.",
        risk="read_only",
        input_schema={"ui_cases": "array", "setup_result": "object", "post_setup_snapshot": "string"},
        output_schema={"decisions": "array"},
    ),
    ToolCapability(
        name="api.safe_write_gate",
        layer="api",
        skill="api-contract-testing",
        description="Prevent unsafe write requests unless the user explicitly allows write_allowed policy.",
        risk="safety_gate",
        input_schema={"method": "string", "policy": "string"},
        output_schema={"allowed": "boolean", "reason": "string"},
    ),
    ToolCapability(
        name="api.generate_mock_json_body",
        layer="api",
        skill="api-mock-data-generation",
        description="Generate realistic JSON request bodies from OpenAPI requestBody schemas using Faker and field-name heuristics.",
        risk="data_mutation_input",
        input_schema={"request_body_schema": "object", "required_fields": "array", "content_type": "string"},
        output_schema={"body_shape": "object", "fields": "array", "source": "faker_json_schema"},
    ),
    ToolCapability(
        name="api.http_request",
        layer="api",
        skill="api-contract-testing",
        description="Execute HTTP requests with configured timeout, retry, headers, query parameters, and JSON body.",
        risk="network",
        input_schema={"method": "string", "url": "string", "headers": "object", "body": "object"},
        output_schema={"status_code": "number", "elapsed_ms": "number", "body": "object|string"},
    ),
    ToolCapability(
        name="api.status_assert",
        layer="api",
        skill="api-contract-testing",
        description="Assert HTTP status code and common JSON envelope status fields.",
        risk="read_only",
        input_schema={"expected": "number|array", "actual": "number", "payload": "object|string"},
        output_schema={"passed": "boolean", "actual": "array"},
    ),
    ToolCapability(
        name="api.json_path_assert",
        layer="api",
        skill="api-contract-testing",
        description="Assert JSON path existence, equality, non-null, and contains rules.",
        risk="read_only",
        input_schema={"path": "string", "expected": "any", "operator": "string"},
        output_schema={"passed": "boolean", "actual": "any"},
    ),
    ToolCapability(
        name="api.schema_assert",
        layer="api",
        skill="api-contract-testing",
        description="Validate response payload against an OpenAPI/JSON schema.",
        risk="read_only",
        input_schema={"schema": "object", "payload": "object"},
        output_schema={"passed": "boolean", "error": "string"},
    ),
    ToolCapability(
        name="api.extract_value",
        layer="api",
        skill="api-chain-orchestration",
        description="Extract values from an upstream response so later requests can reuse them.",
        risk="credential_sensitive",
        input_schema={"path": "string", "name": "string"},
        output_schema={"extracted": "boolean", "name": "string"},
    ),
    ToolCapability(
        name="api.inject_dependency",
        layer="api",
        skill="api-chain-orchestration",
        description="Inject extracted variables into URL, headers, query params, or request body placeholders.",
        risk="credential_sensitive",
        input_schema={"template": "object|string", "variables": "object"},
        output_schema={"resolved": "boolean"},
    ),
    ToolCapability(
        name="ui.playwright_cli",
        layer="ui",
        skill="browser-ui-testing",
        description="Run playwright-cli commands such as open, click, fill, snapshot, screenshot, and state operations.",
        risk="browser",
        input_schema={"command": "string", "session": "string"},
        output_schema={"status_code": "number", "stdout": "string", "stderr": "string"},
    ),
    ToolCapability(
        name="ui.smart_wait",
        layer="ui",
        skill="browser-ui-testing",
        description="Use Playwright load-state waiting after navigation or actions instead of hard sleep commands.",
        risk="browser",
        input_schema={"timeout_ms": "number", "after_command": "string"},
        output_schema={"status_code": "number"},
    ),
    ToolCapability(
        name="ui.snapshot_assert",
        layer="ui",
        skill="browser-ui-testing",
        description="Assert that the accessible page snapshot contains expected text.",
        risk="read_only",
        input_schema={"expected": "string", "snapshot": "string"},
        output_schema={"passed": "boolean"},
    ),
    ToolCapability(
        name="reporter.failure_analysis",
        layer="reporter",
        skill="test-reporting",
        description="Summarize failures, classify likely causes, and produce recommendations.",
        risk="read_only",
        input_schema={"api_results": "object", "ui_results": "object", "tool_calls": "array"},
        output_schema={"summary": "string", "bugs_found": "array", "recommendations": "array"},
    ),
)


AUTOMATION_SKILLS: tuple[AutomationSkill, ...] = (
    AutomationSkill(
        name="test-planning",
        layer="planner",
        description="Plan test scope, priorities, dependencies, and rerun/skip strategy from user intent and discovered assets.",
        triggers=["any run", "OpenAPI input", "URL input", "selected suite"],
        tools=[
            "planner.parse_requirement",
            "planner.generate_execution_plan",
            "planner.analyze_ui_execution_context",
        ],
    ),
    AutomationSkill(
        name="api-contract-testing",
        layer="api",
        description="Run traditional API automation: request construction, response assertions, schema checks, and safe write gating.",
        triggers=["api", "full", "auto with OpenAPI", "selected API cases"],
        tools=["api.safe_write_gate", "api.http_request", "api.status_assert", "api.json_path_assert", "api.schema_assert"],
    ),
    AutomationSkill(
        name="api-mock-data-generation",
        layer="api",
        description="Generate schema-aware JSON request bodies for write_allowed or authenticated API checks that need request payloads.",
        triggers=["OpenAPI requestBody", "write_allowed API run", "API case with request body schema"],
        tools=["api.generate_mock_json_body"],
    ),
    AutomationSkill(
        name="api-chain-orchestration",
        layer="api",
        description="Orchestrate API dependencies by extracting values from upstream responses and injecting them into downstream requests.",
        triggers=["cases with depends_on or extract", "auth chain", "data-driven API suites"],
        tools=["api.extract_value", "api.inject_dependency"],
    ),
    AutomationSkill(
        name="browser-ui-testing",
        layer="ui",
        description="Run UI automation through playwright-cli with snapshots, actions, smart waits, screenshots, and browser state reuse.",
        triggers=["ui", "full", "auto with URL", "pre-test setup instructions"],
        tools=["ui.playwright_cli", "ui.smart_wait", "ui.snapshot_assert"],
    ),
    AutomationSkill(
        name="test-reporting",
        layer="reporter",
        description="Build a traceable report from test results, artifacts, tool calls, and failure evidence.",
        triggers=["any completed run"],
        tools=["reporter.failure_analysis"],
    ),
)


def build_tool_registry() -> dict[str, Any]:
    return {
        "version": "2026-05-20",
        "tools": [asdict(tool) for tool in TOOL_CAPABILITIES],
        "skills": [asdict(skill) for skill in AUTOMATION_SKILLS],
    }


def select_skills_for_state(state: dict[str, Any]) -> list[dict[str, Any]]:
    test_type = str(state.get("test_type") or "auto").lower()
    input_type = str(state.get("input_type") or "unknown").lower()
    has_api = bool(state.get("parsed_api_schema") or state.get("api_cases") or state.get("base_url_override"))
    has_ui = input_type == "url" or bool(state.get("ui_seed_url") or state.get("ui_cases"))
    has_setup = bool((state.get("setup_instructions") or state.get("login_instructions") or "").strip())
    auth_chain = state.get("auth_chain") if isinstance(state.get("auth_chain"), dict) else {}
    has_auth_chain = bool(
        has_api
        and auth_chain
        and str(auth_chain.get("auth_type") or "unknown").lower() not in {"none", "unknown", ""}
        and auth_chain.get("credentials")
    )
    has_case_chain = any(
        isinstance(case, dict) and (case.get("depends_on") or case.get("extract"))
        for case in (state.get("api_cases") or [])
    )
    has_chain = has_auth_chain or has_case_chain
    has_request_body_schema = any(
        isinstance(endpoint, dict) and endpoint.get("request_body_schema")
        for endpoint in (state.get("parsed_api_schema") or [])
    ) or any(
        isinstance(case, dict)
        and (
            case.get("request_body_schema")
            or (
                isinstance(case.get("request_template"), dict)
                and case["request_template"].get("request_body_schema")
            )
        )
        for case in (state.get("api_cases") or [])
    )

    selected = ["test-planning"]
    if test_type in {"api", "full"} or (test_type == "auto" and has_api):
        selected.append("api-contract-testing")
    if has_api and has_request_body_schema:
        selected.append("api-mock-data-generation")
    if has_chain:
        selected.append("api-chain-orchestration")
    if test_type in {"ui", "full"} or (test_type == "auto" and has_ui) or has_setup:
        selected.append("browser-ui-testing")
    selected.append("test-reporting")

    skills_by_name = {skill.name: skill for skill in AUTOMATION_SKILLS}
    return [asdict(skills_by_name[name]) for name in selected if name in skills_by_name]


def install_tool_context(state: dict[str, Any]) -> None:
    state["tool_registry"] = build_tool_registry()
    state["skill_plan"] = select_skills_for_state(state)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact_command(command: str) -> str:
    text = command.strip()
    parts = text.split(maxsplit=1)
    if not parts:
        return text
    name = parts[0].lower()
    if name in {"fill", "type", "press", "cookie-set"} and len(parts) > 1:
        return f"{parts[0]} [REDACTED_INPUT]"
    return text


def _sanitize_tool_summary(value: Any) -> Any:
    value = redact_sensitive_data(value)
    if isinstance(value, dict):
        sanitized = {}
        for key, child in value.items():
            if str(key) in {"command", "source_command", "normalized_command"} and isinstance(child, str):
                sanitized[key] = _redact_command(child)
            else:
                sanitized[key] = _sanitize_tool_summary(child)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_tool_summary(item) for item in value]
    return value


def record_tool_call(
    state: dict[str, Any],
    *,
    tool_name: str,
    layer: str,
    status: str,
    input_summary: dict[str, Any] | None = None,
    output_summary: dict[str, Any] | None = None,
    elapsed_ms: float | int | None = None,
    case_index: int | None = None,
    case_title: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    call = {
        "tool": tool_name,
        "layer": layer,
        "status": status,
        "timestamp": utc_now_iso(),
    }
    if input_summary:
        call["input"] = _sanitize_tool_summary(input_summary)
    if output_summary:
        call["output"] = _sanitize_tool_summary(output_summary)
    if elapsed_ms is not None:
        call["elapsed_ms"] = round(float(elapsed_ms), 2)
    if case_index is not None:
        call["case_index"] = case_index
    if case_title:
        call["case_title"] = case_title
    if metadata:
        call["metadata"] = metadata

    calls = state.setdefault("tool_calls", [])
    calls.append(call)
    if len(calls) > 1000:
        del calls[:-1000]
    return call


def summarize_tool_calls(tool_calls: list[dict[str, Any]] | None) -> dict[str, Any]:
    calls = tool_calls or []
    by_layer: dict[str, dict[str, int]] = {}
    by_tool: dict[str, dict[str, int]] = {}
    for call in calls:
        layer = str(call.get("layer") or "unknown")
        tool = str(call.get("tool") or "unknown")
        status = str(call.get("status") or "unknown")
        by_layer.setdefault(layer, {"total": 0, "success": 0, "failed": 0, "skipped": 0})
        by_tool.setdefault(tool, {"total": 0, "success": 0, "failed": 0, "skipped": 0})
        for bucket in (by_layer[layer], by_tool[tool]):
            bucket["total"] += 1
            if status in {"success", "passed", "done"}:
                bucket["success"] += 1
            elif status == "skipped":
                bucket["skipped"] += 1
            elif status in {"failed", "error"}:
                bucket["failed"] += 1

    return {
        "total": len(calls),
        "by_layer": by_layer,
        "by_tool": by_tool,
    }

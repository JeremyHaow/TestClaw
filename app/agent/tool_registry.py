from __future__ import annotations

from dataclasses import asdict, dataclass, field
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
    timeout_ms: int = 8000
    retry_policy: dict[str, Any] = field(default_factory=lambda: {"max_attempts": 1})
    redaction_policy: list[str] = field(
        default_factory=lambda: ["headers", "token", "cookie", "password", "captcha", "secret"]
    )
    permission_required: str = "none"


@dataclass(frozen=True)
class AutomationSkill:
    name: str
    layer: str
    description: str
    triggers: list[str]
    tools: list[str]
    required_inputs: list[str] = field(default_factory=list)
    expected_observations: list[str] = field(default_factory=list)
    failure_recovery: list[str] = field(default_factory=list)
    safety_constraints: list[str] = field(default_factory=list)


JSON_SCHEMA_PRIMITIVES = {"string", "number", "integer", "boolean", "array", "object", "null"}


def _schema_property(contract: Any) -> dict[str, Any]:
    if isinstance(contract, dict):
        if contract.get("type") or contract.get("properties") or contract.get("oneOf") or contract.get("anyOf"):
            return dict(contract)
        return {"type": "object", "additionalProperties": True}

    text = str(contract or "string").strip()
    normalized = text.lower().replace("optional ", "")
    if normalized in {"any", "*"}:
        return {}

    tokens = [token.strip() for token in normalized.split("|") if token.strip()]
    if tokens and all(token in JSON_SCHEMA_PRIMITIVES for token in tokens):
        types = ["null" if token == "null" else token for token in tokens]
        schema: dict[str, Any] = {"type": types[0] if len(types) == 1 else sorted(set(types))}
        if "array" in types:
            schema.setdefault("items", {})
        if "object" in types:
            schema.setdefault("additionalProperties", True)
        return schema

    enum_tokens = [token.strip() for token in text.split("|") if token.strip()]
    if len(enum_tokens) > 1:
        return {"type": "string", "enum": enum_tokens}
    if normalized in JSON_SCHEMA_PRIMITIVES:
        return _schema_property(normalized)
    return {"type": "string", "description": text}


def _strict_object_schema(contract: dict[str, Any]) -> dict[str, Any]:
    if contract.get("type") == "object" and isinstance(contract.get("properties"), dict):
        strict = dict(contract)
        strict.setdefault("required", sorted(strict["properties"]))
        strict.setdefault("additionalProperties", False)
        return strict
    properties = {str(key): _schema_property(value) for key, value in contract.items()}
    return {
        "type": "object",
        "properties": properties,
        "required": sorted(properties),
        "additionalProperties": False,
    }


def _tool_contract(tool: ToolCapability) -> dict[str, Any]:
    payload = asdict(tool)
    payload["input_schema"] = _strict_object_schema(payload["input_schema"])
    payload["output_schema"] = _strict_object_schema(payload["output_schema"])
    payload["schema_contract"] = "strict_json_schema"
    return payload


TOOL_CAPABILITIES: tuple[ToolCapability, ...] = (
    ToolCapability(
        name="agent.create_mission_plan",
        layer="supervisor",
        skill="agent-supervision",
        description="Decompose the user objective into mission subgoals, active roles, memory needs, environment observations, and execution success criteria.",
        risk="read_only",
        input_schema={"objective": "string", "input_type": "string", "test_type": "string"},
        output_schema={"agent_mission_plan": "object", "agent_roster": "array", "agent_delegation_trace": "array"},
    ),
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
        name="planner.select_agent_strategy",
        layer="planner",
        skill="test-planning",
        description="Select a structured model-driven testing strategy and ordered tool plan, then validate it against schema, auth readiness, and safety policy.",
        risk="read_only",
        input_schema={
            "objective": "string",
            "api_schema_summary": "array",
            "api_execution_policy": "safe_read_only|safe_with_auth|write_allowed",
        },
        output_schema={
            "agent_strategy_decision": "object",
            "agent_tool_plan": "array",
            "agent_actions": "array",
            "agent_action_observations": "array",
            "agent_strategy_diagnostics": "array",
        },
    ),
    ToolCapability(
        name="planner.generate_test_cases",
        layer="planner",
        skill="test-planning",
        description="Generate mission-aligned API and UI cases from plans, schema, memory, and environment observations.",
        risk="read_only",
        input_schema={"agent_mission_plan": "object", "api_plan": "object", "ui_plan": "object"},
        output_schema={"api_cases": "array", "ui_cases": "array"},
    ),
    ToolCapability(
        name="memory.retrieve_rag_context",
        layer="memory",
        skill="rag-knowledge-retrieval",
        description="Retrieve redacted historical testing knowledge by vector similarity and feed the most relevant snippets into planning and case generation.",
        risk="read_only",
        input_schema={"objective": "string", "target": "string", "schema_paths": "array"},
        output_schema={
            "rag_context": "string",
            "sources": "array",
            "match_count": "number",
            "mode": "vector|lexical_fallback|unavailable",
            "vector_source_count": "number",
            "fallback_reason": "string|null",
        },
    ),
    ToolCapability(
        name="memory.retrieve",
        layer="memory",
        skill="quality-memory-reuse",
        description="Retrieve prior failures, selectors, auth notes, risky surfaces, and reusable test assets with local redaction and source metadata.",
        risk="read_only",
        input_schema={"query": "string", "target": "string", "limit": "number"},
        output_schema={"sources": "array", "summary": "string", "redacted": "boolean"},
    ),
    ToolCapability(
        name="auth.discover_candidates",
        layer="auth",
        skill="api-auth-discovery",
        description="Inspect OpenAPI paths, operations, request schemas, response schemas, and security schemes to find login/token/session/csrf/captcha and read-only validation candidates.",
        risk="credential_sensitive",
        input_schema={"source": "string", "target_url": "string", "credential_fields": "array"},
        output_schema={"login_candidates": "array", "validation_candidates": "array", "missing_inputs": "array"},
        redaction_policy=["credentials", "headers", "token", "cookie", "password", "captcha", "csrf"],
    ),
    ToolCapability(
        name="auth.try_login",
        layer="auth",
        skill="api-auth-discovery",
        description="Execute a locally validated login/token/session request using credential fields mapped from schema names and configured safety limits.",
        risk="network_credential",
        input_schema={"method": "POST|PUT|PATCH", "url": "string", "body": "object", "headers": "object"},
        output_schema={"ok": "boolean", "status_code": "number", "response_shape": "object"},
        permission_required="credentials_present",
        redaction_policy=["body", "headers", "token", "cookie", "password", "captcha", "csrf"],
    ),
    ToolCapability(
        name="auth.extract_token_or_cookie",
        layer="auth",
        skill="api-auth-discovery",
        description="Extract token, authorization header, session id, cookie, or Set-Cookie value from nested/cased response payloads and headers.",
        risk="credential_sensitive",
        input_schema={"response_payload": "object", "headers": "object", "token_path": "string|null"},
        output_schema={"header_name": "string|null", "extracted": "boolean", "source": "string"},
        redaction_policy=["response_payload", "headers", "token", "cookie", "session"],
    ),
    ToolCapability(
        name="auth.validate_readonly",
        layer="auth",
        skill="api-auth-discovery",
        description="Validate resolved auth material only against protected documented GET/HEAD/OPTIONS endpoints.",
        risk="network_read_only",
        input_schema={"candidates": "array", "headers": "object"},
        output_schema={"success_count": "number", "validation_results": "array"},
        permission_required="read_only_endpoint",
        redaction_policy=["headers", "token", "cookie", "authorization"],
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
        name="intake.update_step",
        layer="planner",
        skill="intake-planning",
        description="Persist a structured intake step without creating an ordinary chat message.",
        risk="read_only",
        input_schema={"session_id": "string", "step": "string", "choice": "object", "supplement": "string"},
        output_schema={"current_step": "string", "draft": "object", "ready_to_generate": "boolean"},
    ),
    ToolCapability(
        name="intake.generate_plan",
        layer="planner",
        skill="intake-planning",
        description="Generate a run payload from structured target, scope, auth, safety, and success criteria intake state.",
        risk="read_only",
        input_schema={"intake_state": "object"},
        output_schema={"plan": "object", "run_payload": "object", "missing_info": "array"},
    ),
    ToolCapability(
        name="human.ask",
        layer="supervisor",
        skill="human-intervention",
        description="Ask the user for the smallest missing field, approval, captcha, credential route, or environment choice needed to continue.",
        risk="human_input",
        input_schema={"missing_fields": "array", "reason": "string", "choices": "array"},
        output_schema={"question": "string", "blocking": "boolean", "requested_fields": "array"},
        redaction_policy=["reason", "choices", "missing_fields"],
    ),
    ToolCapability(
        name="planner.evaluate_execution_evidence",
        layer="planner",
        skill="test-planning",
        description="Evaluate API/UI execution evidence, decide whether evidence is sufficient, and choose bounded report/continue/replan actions.",
        risk="read_only",
        input_schema={"stage": "api|ui", "evidence_summary": "object", "tool_calls": "array"},
        output_schema={
            "sufficient_evidence": "boolean",
            "next_action": "report|continue_to_ui|replan_api|replan_ui",
            "diagnostics": "array",
            "replan_instructions": "string",
        },
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
        name="api.derive_schema_requests",
        layer="api",
        skill="api-contract-testing",
        description="Derive executable API request candidates from documented OpenAPI endpoints selected by the validated strategy contract.",
        risk="safety_gate",
        input_schema={
            "scope": "all_documented_safe_methods|focused_documented_endpoints|sampled_contract",
            "include": "array",
            "method_policy": "object",
        },
        output_schema={"request_candidates": "array", "request_selection": "object"},
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
        name="ui.open",
        layer="ui",
        skill="browser-ui-exploration",
        description="Open a browser page for safe visual and accessibility exploration.",
        risk="browser_read",
        input_schema={"url": "string", "session": "string"},
        output_schema={"opened": "boolean", "final_url": "string", "title": "string"},
    ),
    ToolCapability(
        name="ui.snapshot",
        layer="ui",
        skill="browser-ui-exploration",
        description="Capture an accessibility snapshot for model-visible UI exploration without exposing secrets.",
        risk="browser_read",
        input_schema={"session": "string"},
        output_schema={"snapshot": "string", "element_count": "number"},
        redaction_policy=["snapshot", "input_values"],
    ),
    ToolCapability(
        name="ui.click",
        layer="ui",
        skill="browser-ui-exploration",
        description="Click a validated visible element selector or accessibility ref inside the browser session.",
        risk="browser_action",
        input_schema={"selector_or_ref": "string", "session": "string"},
        output_schema={"clicked": "boolean", "observation": "string"},
        permission_required="safe_ui_action",
    ),
    ToolCapability(
        name="ui.fill",
        layer="ui",
        skill="browser-ui-exploration",
        description="Fill a validated form field with redacted input handling.",
        risk="credential_sensitive",
        input_schema={"selector_or_ref": "string", "value": "string", "session": "string"},
        output_schema={"filled": "boolean", "observation": "string"},
        redaction_policy=["value", "selector_or_ref"],
        permission_required="safe_ui_action",
    ),
    ToolCapability(
        name="ui.screenshot",
        layer="ui",
        skill="browser-ui-exploration",
        description="Capture a screenshot artifact for evidence and later report synthesis.",
        risk="browser_read",
        input_schema={"session": "string", "label": "string"},
        output_schema={"path": "string", "stored": "boolean"},
    ),
    ToolCapability(
        name="ui.assert_visible",
        layer="ui",
        skill="browser-ui-exploration",
        description="Assert that expected text or element state is visible in the browser surface.",
        risk="read_only",
        input_schema={"expected": "string", "snapshot": "string"},
        output_schema={"passed": "boolean", "reason": "string"},
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
    ToolCapability(
        name="evidence.evaluate",
        layer="reporter",
        skill="evidence-evaluation",
        description="Evaluate whether current observations are enough to report, continue, replan, or request human input.",
        risk="read_only",
        input_schema={"observations": "array", "success_criteria": "array", "tool_calls": "array"},
        output_schema={"sufficient": "boolean", "next_action": "string", "missing_evidence": "array"},
    ),
)


AUTOMATION_SKILLS: tuple[AutomationSkill, ...] = (
    AutomationSkill(
        name="agent-supervision",
        layer="supervisor",
        description="Create and maintain the mission-level Plan-Execute/ReAct control artifact and role delegation trace.",
        triggers=["any run", "complex objective", "multi-step mission"],
        tools=["agent.create_mission_plan"],
        required_inputs=["objective", "input_type", "test_type"],
        expected_observations=["mission subgoals", "role roster", "delegation trace"],
        failure_recovery=["Continue with a compact default mission plan when model planning is unavailable."],
        safety_constraints=["visible_reason_only", "redact_tool_context"],
    ),
    AutomationSkill(
        name="test-planning",
        layer="planner",
        description="Plan test scope, priorities, dependencies, and rerun/skip strategy from user intent and discovered assets.",
        triggers=["any run", "OpenAPI input", "URL input", "selected suite"],
        tools=[
            "planner.parse_requirement",
            "planner.generate_execution_plan",
            "planner.select_agent_strategy",
            "planner.generate_test_cases",
            "planner.analyze_ui_execution_context",
            "planner.evaluate_execution_evidence",
        ],
        required_inputs=["objective", "target", "safety_policy"],
        expected_observations=["strategy contract", "validated tool plan"],
        failure_recovery=["Use deterministic fallback strategy when planner output is invalid."],
        safety_constraints=["strict_json", "local_normalization", "redact_secrets"],
    ),
    AutomationSkill(
        name="intake-planning",
        layer="planner",
        description="Collect target, scope, auth, safety, and success criteria as structured intake state before creating a run.",
        triggers=["agent plan mode", "clarifying question", "structured choice"],
        tools=["intake.update_step", "intake.generate_plan"],
        required_inputs=["session_id", "current_step"],
        expected_observations=["draft state", "next missing field", "run payload readiness"],
        failure_recovery=["Ask for the smallest missing intake field instead of sending a fake chat turn."],
        safety_constraints=["no_secret_echo", "supported_api_or_web_ui_only"],
    ),
    AutomationSkill(
        name="rag-knowledge-retrieval",
        layer="memory",
        description="Vector-retrieve prior bug knowledge and tester notes, then inject relevant context into LangChain planner/case-generator prompts.",
        triggers=["any run with matching knowledge", "re-run on known target", "historical failure themes"],
        tools=["memory.retrieve_rag_context"],
        required_inputs=["objective", "target"],
        expected_observations=["redacted sources", "retrieval mode", "source count"],
        failure_recovery=["Continue with explicit no-memory observation when retrieval is unavailable."],
        safety_constraints=["redacted_context", "bounded_token_budget"],
    ),
    AutomationSkill(
        name="quality-memory-reuse",
        layer="memory",
        description="Reuse prior failures, selectors, auth notes, known risky surfaces, and accepted test assets as bounded planning context.",
        triggers=["quality memory handoff", "repeat target", "historical blocker", "asset reuse"],
        tools=["memory.retrieve"],
        required_inputs=["target", "objective"],
        expected_observations=["memory summary", "source metadata", "redaction status"],
        failure_recovery=["Proceed without memory while recording that no reusable context was found."],
        safety_constraints=["do_not_reuse_secret_values", "source_metadata_only"],
    ),
    AutomationSkill(
        name="api-auth-discovery",
        layer="auth",
        description="Infer and validate login/token/session/cookie/captcha/csrf routes from OpenAPI and user-provided credentials.",
        triggers=["api auth required", "credentials present", "manual token stale", "security scheme detected"],
        tools=[
            "auth.discover_candidates",
            "auth.try_login",
            "auth.extract_token_or_cookie",
            "auth.validate_readonly",
        ],
        required_inputs=["OpenAPI schema", "target_url", "credentials or explicit manual auth"],
        expected_observations=["login candidate", "token/cookie source", "protected read-only validation"],
        failure_recovery=["Ask only for missing login endpoint, field name, captcha/csrf, token path, or validation endpoint."],
        safety_constraints=["safe_readonly_validation_only", "redact_credentials", "no_write_methods"],
    ),
    AutomationSkill(
        name="api-contract-testing",
        layer="api",
        description="Run traditional API automation: request construction, response assertions, schema checks, and safe write gating.",
        triggers=["api", "full", "auto with OpenAPI", "selected API cases"],
        tools=[
            "api.safe_write_gate",
            "api.derive_schema_requests",
            "api.http_request",
            "api.status_assert",
            "api.json_path_assert",
            "api.schema_assert",
        ],
        required_inputs=["OpenAPI schema or selected API cases", "execution policy"],
        expected_observations=["request selection", "HTTP evidence", "assertion results"],
        failure_recovery=["Record skipped endpoints with guardrail diagnostics."],
        safety_constraints=["schema_only", "safe_methods_only_by_default"],
    ),
    AutomationSkill(
        name="api-mock-data-generation",
        layer="api",
        description="Generate schema-aware JSON request bodies for write_allowed or authenticated API checks that need request payloads.",
        triggers=["OpenAPI requestBody", "write_allowed API run", "API case with request body schema"],
        tools=["api.generate_mock_json_body"],
        required_inputs=["request body schema", "required fields"],
        expected_observations=["generated field list", "body shape"],
        failure_recovery=["Skip body synthesis and record missing schema when the request schema is invalid."],
        safety_constraints=["no_secret_seed_values", "schema_constrained_generation"],
    ),
    AutomationSkill(
        name="api-chain-orchestration",
        layer="api",
        description="Orchestrate API dependencies by extracting values from upstream responses and injecting them into downstream requests.",
        triggers=["cases with depends_on or extract", "auth chain", "data-driven API suites"],
        tools=["api.extract_value", "api.inject_dependency"],
        required_inputs=["upstream evidence", "dependency map"],
        expected_observations=["extracted variable names", "resolved downstream templates"],
        failure_recovery=["Skip dependent requests with an explicit missing dependency observation."],
        safety_constraints=["redact_extracted_sensitive_values", "schema_only_dependencies"],
    ),
    AutomationSkill(
        name="browser-ui-testing",
        layer="ui",
        description="Run UI automation through playwright-cli with snapshots, actions, smart waits, screenshots, and browser state reuse.",
        triggers=["ui", "full", "auto with URL", "pre-test setup instructions"],
        tools=["ui.playwright_cli", "ui.smart_wait", "ui.snapshot_assert"],
        required_inputs=["url or authenticated context"],
        expected_observations=["command result", "snapshot", "screenshot"],
        failure_recovery=["Ask for login/setup context or replan from fresh snapshot."],
        safety_constraints=["redact_fill_values", "avoid_destructive_actions"],
    ),
    AutomationSkill(
        name="browser-ui-exploration",
        layer="ui",
        description="Explore live browser surfaces with open, snapshot, click, fill, screenshot, and visible assertions under local UI action guardrails.",
        triggers=["ui exploration", "browser surface unknown", "need screenshot evidence", "form workflow"],
        tools=["ui.open", "ui.snapshot", "ui.click", "ui.fill", "ui.screenshot", "ui.assert_visible"],
        required_inputs=["url", "safe action policy"],
        expected_observations=["page title", "snapshot", "screenshot path", "visible assertion"],
        failure_recovery=["Ask for selector, login context, captcha handling, or approval when blocked."],
        safety_constraints=["redact_input_values", "block_destructive_actions_without_policy"],
    ),
    AutomationSkill(
        name="evidence-evaluation",
        layer="reporter",
        description="Judge whether collected API/UI/tool evidence is enough to report, continue, replan, or request human input.",
        triggers=["after API execution", "after UI execution", "blocked run", "before report"],
        tools=["evidence.evaluate", "planner.evaluate_execution_evidence"],
        required_inputs=["tool observations", "success criteria"],
        expected_observations=["sufficiency decision", "missing evidence", "next action"],
        failure_recovery=["Use deterministic sufficiency thresholds when model evaluation is unavailable."],
        safety_constraints=["visible_reason_only", "no_hidden_chain_of_thought"],
    ),
    AutomationSkill(
        name="human-intervention",
        layer="supervisor",
        description="Pause or block with the smallest concrete missing input, approval, captcha, credential route, or environment choice.",
        triggers=["auth blocked", "captcha required", "unsafe action requested", "missing target", "environment unavailable"],
        tools=["human.ask"],
        required_inputs=["blocker reason", "missing fields"],
        expected_observations=["requested fields", "blocking status"],
        failure_recovery=["Keep run blocked with actionable next step until user supplies the missing input."],
        safety_constraints=["ask_smallest_missing_info", "redact_context"],
    ),
    AutomationSkill(
        name="test-reporting",
        layer="reporter",
        description="Build a traceable report from test results, artifacts, tool calls, and failure evidence.",
        triggers=["any completed run"],
        tools=["reporter.failure_analysis"],
        required_inputs=["execution results", "evidence", "tool trace"],
        expected_observations=["findings", "recommendations", "reusable assets"],
        failure_recovery=["Report blockers and coverage gaps when execution is incomplete."],
        safety_constraints=["redact_secrets", "evidence_linked_findings"],
    ),
)


def build_tool_registry() -> dict[str, Any]:
    return {
        "version": "2026-05-25",
        "tools": [_tool_contract(tool) for tool in TOOL_CAPABILITIES],
        "skills": [asdict(skill) for skill in AUTOMATION_SKILLS],
    }


def tool_capabilities_by_name() -> dict[str, dict[str, Any]]:
    return {tool.name: _tool_contract(tool) for tool in TOOL_CAPABILITIES}


def allowed_tool_names() -> set[str]:
    return set(tool_capabilities_by_name())


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

    selected = ["agent-supervision", "test-planning"]
    if state.get("rag_retrieval") or state.get("rag_context") or state.get("target_memory"):
        selected.append("rag-knowledge-retrieval")
        selected.append("quality-memory-reuse")
    if test_type in {"api", "full"} or (test_type == "auto" and has_api):
        auth_preflight = state.get("auth_preflight") if isinstance(state.get("auth_preflight"), dict) else {}
        if (
            state.get("auth_credentials")
            or state.get("auth_config")
            or auth_preflight
            or any(isinstance(endpoint, dict) and endpoint.get("auth_required") for endpoint in (state.get("parsed_api_schema") or []))
        ):
            selected.append("api-auth-discovery")
        selected.append("api-contract-testing")
    if has_api and has_request_body_schema:
        selected.append("api-mock-data-generation")
    if has_chain:
        selected.append("api-chain-orchestration")
    if test_type in {"ui", "full"} or (test_type == "auto" and has_ui) or has_setup:
        selected.append("browser-ui-testing")
        selected.append("browser-ui-exploration")
    if (
        isinstance(state.get("auth_preflight"), dict)
        and str(state["auth_preflight"].get("status") or "").lower() == "blocked"
    ) or state.get("last_error"):
        selected.append("human-intervention")
    selected.append("evidence-evaluation")
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


def _role_for_layer(layer: str) -> str:
    return {
        "supervisor": "supervisor_planner",
        "planner": "supervisor_planner",
        "memory": "memory_researcher",
        "auth": "api_executor",
        "api": "api_executor",
        "ui": "ui_explorer",
        "reporter": "reporter",
    }.get(str(layer or "").lower(), "supervisor_planner")


def _default_visible_reason(tool_name: str, layer: str) -> str:
    if tool_name.startswith("agent."):
        return "Create or update the visible mission control plan."
    if tool_name.startswith("memory."):
        return "Retrieve bounded historical context before deciding the next test action."
    if tool_name.startswith("auth."):
        return "Discover and validate credential handling under the local read-only auth policy."
    if tool_name.startswith("human."):
        return "Ask for the smallest missing input needed to continue safely."
    if tool_name.startswith("intake."):
        return "Update structured intake state without turning the choice into chat text."
    if tool_name.startswith("planner.evaluate"):
        return "Compare execution evidence with mission goals and choose the next bounded action."
    if tool_name.startswith("planner."):
        return "Convert mission context into executable testing strategy or cases."
    if tool_name.startswith("api."):
        return "Collect API evidence inside the configured schema and safety policy."
    if tool_name.startswith("ui."):
        return "Observe or operate the browser surface and capture user-visible evidence."
    if tool_name.startswith("reporter."):
        return "Summarize tested coverage, findings, evidence, and next actions."
    return f"Run {layer or 'agent'} tool and record its observation."


def _observation_from_call(call: dict[str, Any]) -> str:
    status = str(call.get("status") or "unknown")
    output = call.get("output")
    if isinstance(output, dict):
        for key in (
            "next_action",
            "next_node",
            "verdict",
            "status_code",
            "mode",
            "source_count",
            "subgoal_count",
            "accepted",
            "selected_total",
            "candidate_total",
        ):
            if key in output and output.get(key) not in (None, ""):
                return f"{status}; {key}={output.get(key)}"
        if output:
            return f"{status}; observed {len(output)} output field(s)"
    return status


def _next_decision_from_call(call: dict[str, Any]) -> str:
    metadata = call.get("metadata") if isinstance(call.get("metadata"), dict) else {}
    if metadata.get("next_decision"):
        return str(metadata["next_decision"])[:240]
    output = call.get("output")
    if isinstance(output, dict):
        if output.get("next_action"):
            return str(output["next_action"])[:240]
        if output.get("next_node"):
            return str(output["next_node"])[:240]
    status = str(call.get("status") or "")
    if status in {"failed", "error"}:
        return "surface_blocker_or_fallback"
    if status == "skipped":
        return "continue_without_this_tool"
    return "continue_mission"


def _append_react_trace(state: dict[str, Any], call: dict[str, Any]) -> None:
    metadata = call.get("metadata") if isinstance(call.get("metadata"), dict) else {}
    layer = str(call.get("layer") or "")
    tool_name = str(call.get("tool") or "unknown")
    trace = {
        "actor": metadata.get("actor") or _role_for_layer(layer),
        "reason": str(metadata.get("reason") or _default_visible_reason(tool_name, layer))[:360],
        "action": tool_name,
        "tool": tool_name,
        "status": call.get("status"),
        "observation": _observation_from_call(call)[:360],
        "evidence": call.get("output") or {},
        "next_decision": _next_decision_from_call(call),
        "timestamp": call.get("timestamp"),
    }
    if call.get("case_index") is not None:
        trace["case_index"] = call.get("case_index")
    if call.get("case_title"):
        trace["case_title"] = call.get("case_title")

    traces = state.setdefault("agent_react_trace", [])
    traces.append(redact_sensitive_data(trace))
    if len(traces) > 500:
        del traces[:-500]


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
        call["metadata"] = _sanitize_tool_summary(metadata)

    calls = state.setdefault("tool_calls", [])
    calls.append(call)
    if len(calls) > 1000:
        del calls[:-1000]
    _append_react_trace(state, call)
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

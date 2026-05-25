from __future__ import annotations

import re
from copy import deepcopy
from typing import Any
from urllib.parse import urlsplit

SAFE_API_METHODS = {"GET", "HEAD", "OPTIONS"}
WRITE_API_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_JSON_PATH_TOKEN_RE = re.compile(r"([A-Za-z0-9_\-]+)|\[(\d+)\]")
_JSON_TYPE_ALIASES = {
    "is_object": "object",
    "object": "object",
    "json_object": "object",
    "is_array": "array",
    "array": "array",
    "list": "array",
    "is_string": "string",
    "string": "string",
    "str": "string",
    "text": "string",
    "is_number": "number",
    "number": "number",
    "numeric": "number",
    "is_integer": "integer",
    "integer": "integer",
    "int": "integer",
    "is_boolean": "boolean",
    "boolean": "boolean",
    "bool": "boolean",
    "is_null": "null",
    "null": "null",
    "none": "null",
}
_TYPE_ASSERTION_OPERATORS = {"", "equals", "==", "is", "type", "json_type"}
_MISSION_CONTROL_ASSERTION_TERMS = {
    "agent",
    "worker",
    "session",
    "planning",
    "planner",
    "mission",
    "orchestration",
    "react",
    "delegation",
    "subgoal",
    "tool_call",
    "tool calls",
    "db_session",
    "database session",
    "event loop",
    "asyncpg",
    "celery",
    "langgraph",
}


def _normalize_method(value: Any) -> str:
    return str(value or "GET").strip().upper()


def _normalize_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith(("http://", "https://")):
        parsed = urlsplit(text)
        text = parsed.path or "/"
    text = text.split("?", 1)[0].split("#", 1)[0]
    if not text.startswith("/"):
        text = f"/{text}"
    if len(text) > 1:
        text = text.rstrip("/")
    return text or "/"


def _path_template_re(path_template: str) -> re.Pattern[str]:
    escaped = re.escape(_normalize_path(path_template))
    escaped = re.sub(r"\\\{[^/{}]+\\\}", r"[^/]+", escaped)
    return re.compile(f"^{escaped}$")


def _request_template_from_case(case: dict[str, Any]) -> dict[str, Any]:
    direct = case.get("request_template")
    if isinstance(direct, dict):
        return direct
    test_data = case.get("test_data")
    if isinstance(test_data, dict):
        nested = test_data.get("request_template")
        if isinstance(nested, dict):
            return nested
        if any(key in test_data for key in ("method", "url", "path", "endpoint", "base_url")):
            return test_data
    return {}


def _case_method_and_path(case: dict[str, Any]) -> tuple[str, str]:
    tmpl = _request_template_from_case(case)
    method = _normalize_method(tmpl.get("method") or case.get("method"))
    raw_path = (
        tmpl.get("path")
        or tmpl.get("endpoint")
        or case.get("endpoint")
        or tmpl.get("url")
        or case.get("url")
    )
    return method, _normalize_path(raw_path)


def _endpoint_match(
    case: dict[str, Any],
    schema_endpoints: list[dict[str, Any]],
) -> dict[str, Any] | None:
    method, path = _case_method_and_path(case)
    if not path:
        return None
    for endpoint in schema_endpoints:
        if _normalize_method(endpoint.get("method")) != method:
            continue
        endpoint_path = _normalize_path(endpoint.get("path"))
        if path == endpoint_path or _path_template_re(endpoint_path).match(path):
            return endpoint
    return None


def _response_schema_property(schema: dict[str, Any], name: str) -> dict[str, Any] | None:
    if not isinstance(schema, dict):
        return None
    properties = schema.get("properties")
    if isinstance(properties, dict) and isinstance(properties.get(name), dict):
        return properties[name]
    return None


def _schema_for_json_path(path_expr: str, schema: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(schema, dict):
        return None
    path = str(path_expr or "").strip()
    if path in {"", "$"}:
        return schema
    if path.startswith("$"):
        path = path[1:]
    path = path.lstrip(".")
    if not path:
        return schema

    current = schema
    for raw_part in path.split("."):
        part = raw_part.split("[", 1)[0]
        if not part:
            continue
        if current.get("type") == "array" and isinstance(current.get("items"), dict):
            current = current["items"]
        child = _response_schema_property(current, part)
        if child is None:
            return None
        current = child
    return current


def _json_path_grounded_in_schema(path_expr: str, schema: dict[str, Any] | None) -> bool:
    return _schema_for_json_path(path_expr, schema) is not None


def _json_path_is_root(path_expr: str) -> bool:
    path = str(path_expr or "").strip()
    return path in {"", "$"}


def _json_path_get(payload: Any, path_expr: str, missing: object) -> Any:
    path = str(path_expr or "").strip()
    if path.startswith("$"):
        path = path[1:]
    path = path.lstrip(".")
    if not path:
        return payload

    current = payload
    for raw_part in path.split("."):
        if not raw_part:
            continue
        matches = list(_JSON_PATH_TOKEN_RE.finditer(raw_part))
        if not matches:
            return missing
        for match in matches:
            key, index = match.groups()
            if key is not None:
                if not isinstance(current, dict) or key not in current:
                    return missing
                current = current[key]
            else:
                if not isinstance(current, list):
                    return missing
                idx = int(index)
                if idx >= len(current):
                    return missing
                current = current[idx]
    return current


def _json_path_grounded_in_example(path_expr: str, example: Any) -> bool:
    if example is None or _json_path_is_root(path_expr):
        return False
    missing = object()
    return _json_path_get(example, path_expr, missing) is not missing


def _json_path_field_names(path_expr: str) -> list[str]:
    path = str(path_expr or "").strip()
    if path.startswith("$"):
        path = path[1:]
    path = path.lstrip(".")
    if not path:
        return []

    fields: list[str] = []
    for raw_part in path.split("."):
        for match in _JSON_PATH_TOKEN_RE.finditer(raw_part):
            key, index = match.groups()
            if key is not None:
                fields.append(key)
            elif index is None:
                continue
    return fields


def _normalized_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").lower()).strip()


def _expected_json_type(expected: Any) -> str | None:
    if not isinstance(expected, str):
        return None
    token = expected.strip().lower().replace("-", "_").replace(" ", "_")
    return _JSON_TYPE_ALIASES.get(token)


def _schema_json_type(schema: dict[str, Any] | None) -> str | None:
    if not isinstance(schema, dict):
        return None
    raw_type = schema.get("type")
    if isinstance(raw_type, list):
        for item in raw_type:
            normalized = _expected_json_type(str(item))
            if normalized:
                return normalized
    if isinstance(raw_type, str):
        normalized = _expected_json_type(raw_type)
        if normalized:
            return normalized
    if isinstance(schema.get("properties"), dict) or isinstance(schema.get("required"), list):
        return "object"
    if isinstance(schema.get("items"), dict):
        return "array"
    return None


def _type_matches_schema(expected_type: str, schema_type: str | None) -> bool:
    if schema_type is None:
        return True
    if expected_type == schema_type:
        return True
    return expected_type == "number" and schema_type == "integer"


def _looks_like_mission_control_assertion(
    path_expr: str,
    expected: Any,
    objective: str | None,
) -> bool:
    text = _normalized_text(f"{path_expr} {expected} {objective or ''}")
    return any(term in text for term in _MISSION_CONTROL_ASSERTION_TERMS)


def _json_path_grounded_in_objective(
    path_expr: str,
    assertion: dict[str, Any],
    objective: str | None,
) -> bool:
    if not objective or _json_path_is_root(path_expr):
        return False
    if _looks_like_mission_control_assertion(
        path_expr,
        assertion.get("expected"),
        objective,
    ):
        return False

    fields = _json_path_field_names(path_expr)
    if not fields:
        return False

    objective_text = _normalized_text(objective)
    field = fields[-1].lower()
    field_tokens = [field, *[token for token in re.split(r"[_\-.]+", field) if len(token) >= 2]]
    if not any(token and token in objective_text for token in field_tokens):
        return False

    operator = str(assertion.get("operator") or assertion.get("op") or "").strip().lower()
    expected = assertion.get("expected")
    if operator in {"exists", "present", "not_null", "non_null"} or expected in {None, "not_null"}:
        return True
    if _expected_json_type(expected):
        return False
    if isinstance(expected, (str, int, float, bool)):
        return str(expected).strip().lower() in objective_text
    return False


def _status_expected_values(expected: Any) -> set[int]:
    values = expected if isinstance(expected, list) else [expected]
    parsed: set[int] = set()
    for value in values:
        try:
            parsed.add(int(value))
        except (TypeError, ValueError):
            continue
    return parsed


def _documented_success_status(endpoint: dict[str, Any] | None) -> int:
    if not isinstance(endpoint, dict):
        return 200
    try:
        return int(endpoint.get("response_status") or 200)
    except (TypeError, ValueError):
        return 200


def _diagnostic(
    *,
    kind: str,
    action: str,
    case: dict[str, Any],
    reason: str,
    method: str | None = None,
    path: str | None = None,
    assertion: dict[str, Any] | None = None,
) -> dict[str, Any]:
    diag: dict[str, Any] = {
        "kind": kind,
        "action": action,
        "title": str(case.get("title") or "Generated API case"),
        "method": method or _case_method_and_path(case)[0],
        "path": path or _case_method_and_path(case)[1],
        "reason": reason,
        "severity": "advisory",
    }
    if assertion:
        diag["assertion_type"] = assertion.get("type")
        if assertion.get("path") or assertion.get("json_path"):
            diag["assertion_path"] = assertion.get("path") or assertion.get("json_path")
    return diag


def _assertion_response_example(
    case: dict[str, Any],
    tmpl: dict[str, Any],
    endpoint: dict[str, Any] | None,
) -> Any:
    for source in (case, tmpl, endpoint or {}):
        if not isinstance(source, dict):
            continue
        for key in ("example_response", "response_example"):
            if key in source:
                return source.get(key)
    return None


def _downgrade_assertion(
    assertion: dict[str, Any],
    *,
    reason: str,
    case: dict[str, Any],
    diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    assertion["blocking"] = False
    assertion["advisory"] = True
    assertion["source"] = "agent_scope_guard"
    diagnostics.append(
        _diagnostic(
            kind="unsupported_api_assertion",
            action="downgraded",
            case=case,
            reason=reason,
            assertion=assertion,
        )
    )
    return assertion


def _rewrite_json_type_assertion(
    assertion: dict[str, Any],
    *,
    path_expr: str,
    expected_type: str,
    case: dict[str, Any],
    diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    rewritten = {
        "type": "json_type",
        "path": path_expr,
        "expected": expected_type,
        "blocking": assertion.get("blocking", True),
        "source": "agent_scope_guard",
    }
    diagnostics.append(
        _diagnostic(
            kind="unsupported_api_assertion",
            action="rewritten",
            case=case,
            reason=(
                "Generated JSON-path equality used a JSON type meta token; it was "
                "rewritten to an executable response type assertion grounded in the "
                "OpenAPI response schema."
            ),
            assertion=assertion,
        )
    )
    return rewritten


def _sanitize_assertions(
    case: dict[str, Any],
    endpoint: dict[str, Any] | None,
    *,
    objective: str | None = None,
    downgrade_ungrounded_jsonpath: bool = True,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    sanitized = deepcopy(case)
    diagnostics: list[dict[str, Any]] = []
    tmpl = _request_template_from_case(sanitized)
    response_schema = (
        sanitized.get("response_schema")
        or tmpl.get("response_schema")
        or (endpoint or {}).get("response_schema")
    )
    response_example = _assertion_response_example(sanitized, tmpl, endpoint)
    documented_status = _documented_success_status(endpoint)
    category = str(sanitized.get("category") or "SMOKE").upper()

    if response_schema and not sanitized.get("response_schema"):
        sanitized["response_schema"] = response_schema

    raw_assertions = sanitized.get("assertions")
    if not isinstance(raw_assertions, list):
        raw_assertions = tmpl.get("assertions") if isinstance(tmpl.get("assertions"), list) else []

    next_assertions: list[dict[str, Any]] = []
    for raw_assertion in raw_assertions:
        if not isinstance(raw_assertion, dict):
            continue
        assertion = deepcopy(raw_assertion)
        if assertion.get("source") == "agent_scope_guard" and assertion.get("advisory"):
            next_assertions.append(assertion)
            continue
        atype = str(assertion.get("type") or "").lower()
        if atype in {"status_code", "status"}:
            expected = assertion.get("expected", documented_status)
            expected_values = _status_expected_values(expected)
            if (
                category not in {"AUTH", "PARAM_VALIDATION"}
                and expected_values
                and documented_status not in expected_values
            ):
                diagnostics.append(
                    _diagnostic(
                        kind="unsupported_api_assertion",
                        action="rewritten",
                        case=sanitized,
                        reason=(
                            "Generated status assertion was not grounded in the documented "
                            "success response; it was rewritten to the schema response status."
                        ),
                        assertion=assertion,
                    )
                )
                assertion["expected"] = documented_status
            assertion["type"] = "status_code"
            next_assertions.append(assertion)
            continue

        if atype in {"json_path", "jsonpath"}:
            path_expr = assertion.get("path") or assertion.get("json_path") or "$"
            operator = str(assertion.get("operator") or assertion.get("op") or "").strip().lower()
            expected_type = _expected_json_type(assertion.get("expected"))
            if expected_type and operator in _TYPE_ASSERTION_OPERATORS:
                path_schema = _schema_for_json_path(str(path_expr), response_schema)
                schema_type = _schema_json_type(path_schema)
                if path_schema and _type_matches_schema(expected_type, schema_type):
                    next_assertions.append(
                        _rewrite_json_type_assertion(
                            assertion,
                            path_expr=str(path_expr),
                            expected_type=expected_type,
                            case=sanitized,
                            diagnostics=diagnostics,
                        )
                    )
                    continue
                assertion["type"] = "json_path"
                next_assertions.append(
                    _downgrade_assertion(
                        assertion,
                        reason=(
                            "Generated JSON-path assertion compared a response value to a "
                            "JSON type meta token without matching OpenAPI response schema "
                            "grounding; it was kept as advisory evidence only."
                        ),
                        case=sanitized,
                        diagnostics=diagnostics,
                    )
                )
                continue

            grounded = (
                _json_path_grounded_in_schema(str(path_expr), response_schema)
                or _json_path_grounded_in_example(str(path_expr), response_example)
                or _json_path_grounded_in_objective(str(path_expr), assertion, objective)
            )
            if grounded:
                assertion["type"] = "json_path"
                next_assertions.append(assertion)
                continue
            if not downgrade_ungrounded_jsonpath:
                assertion["type"] = "json_path"
                next_assertions.append(assertion)
                continue
            assertion["type"] = "json_path"
            next_assertions.append(
                _downgrade_assertion(
                    assertion,
                    reason=(
                        "Generated JSON-path assertion was not grounded in the OpenAPI "
                        "response schema, a documented response example, or a directly "
                        "named user objective field/value; it was kept as advisory "
                        "evidence only."
                    ),
                    case=sanitized,
                    diagnostics=diagnostics,
                )
            )
            continue

        if atype in {"json_type", "type"}:
            path_expr = assertion.get("path") or "$"
            expected_type = _expected_json_type(assertion.get("expected"))
            path_schema = _schema_for_json_path(str(path_expr), response_schema)
            schema_type = _schema_json_type(path_schema)
            if expected_type and path_schema and _type_matches_schema(expected_type, schema_type):
                assertion["type"] = "json_type"
                assertion["expected"] = expected_type
                next_assertions.append(assertion)
                continue
            if not downgrade_ungrounded_jsonpath:
                assertion["type"] = "json_type"
                next_assertions.append(assertion)
                continue
            next_assertions.append(
                _downgrade_assertion(
                    assertion,
                    reason=(
                        "Generated JSON type assertion was not grounded in a matching "
                        "OpenAPI response schema; it was kept as advisory evidence only."
                    ),
                    case=sanitized,
                    diagnostics=diagnostics,
                )
            )
            continue

        if atype in {"schema", "schema_valid", "json_schema"} and response_schema:
            assertion["type"] = "schema"
            next_assertions.append(assertion)
            continue

        if atype in {"body_contains", "contains", "schema", "schema_valid", "json_schema"}:
            assertion["blocking"] = False
            assertion["advisory"] = True
            assertion["source"] = "agent_scope_guard"
            diagnostics.append(
                _diagnostic(
                    kind="unsupported_api_assertion",
                    action="downgraded",
                    case=sanitized,
                    reason=(
                        "Generated body/schema assertion was not grounded in a documented "
                        "response schema; it was kept as advisory evidence only."
                    ),
                    assertion=assertion,
                )
            )
            next_assertions.append(assertion)
            continue

        assertion["blocking"] = False
        assertion["advisory"] = True
        assertion["source"] = "agent_scope_guard"
        diagnostics.append(
            _diagnostic(
                kind="unsupported_api_assertion",
                action="downgraded",
                case=sanitized,
                reason=(
                    "Generated assertion type was not supported by the API runner; "
                    "it was kept as advisory evidence only."
                ),
                assertion=assertion,
            )
        )
        next_assertions.append(assertion)

    if next_assertions:
        sanitized["assertions"] = next_assertions
    return sanitized, diagnostics


def validate_generated_api_cases(
    api_cases: list[dict[str, Any]],
    parsed_api_schema: list[dict[str, Any]] | None,
    *,
    execution_policy: str = "safe_read_only",
    allow_out_of_schema: bool = False,
    objective: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    schema_endpoints = [
        endpoint for endpoint in (parsed_api_schema or []) if isinstance(endpoint, dict)
    ]
    diagnostics: list[dict[str, Any]] = []
    if not api_cases:
        return [], diagnostics

    valid_cases: list[dict[str, Any]] = []
    for case in api_cases:
        if not isinstance(case, dict):
            continue
        method, path = _case_method_and_path(case)
        endpoint = _endpoint_match(case, schema_endpoints) if schema_endpoints else None

        if schema_endpoints and not allow_out_of_schema and endpoint is None:
            diagnostics.append(
                _diagnostic(
                    kind="out_of_scope_api_case",
                    action="dropped",
                    case=case,
                    method=method,
                    path=path,
                    reason=(
                        "Generated API case targeted a path/method absent from the loaded "
                        "OpenAPI schema."
                    ),
                )
            )
            continue

        if execution_policy != "write_allowed" and method in WRITE_API_METHODS:
            diagnostics.append(
                _diagnostic(
                    kind="unsafe_api_case",
                    action="dropped",
                    case=case,
                    method=method,
                    path=path,
                    reason=(
                        "Generated API case used a mutation method while the run policy "
                        "allows only safe read-only execution."
                    ),
                )
            )
            continue

        sanitized, assertion_diagnostics = _sanitize_assertions(
            case,
            endpoint,
            objective=objective,
            downgrade_ungrounded_jsonpath=True,
        )
        diagnostics.extend(assertion_diagnostics)
        valid_cases.append(sanitized)

    return valid_cases, diagnostics


def sanitize_api_case_assertions(
    api_cases: list[dict[str, Any]],
    parsed_api_schema: list[dict[str, Any]] | None,
    *,
    objective: str | None = None,
    downgrade_ungrounded_jsonpath: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Sanitize assertions without changing case method/path scope.

    The API runner uses this as a runtime safety net for generated/meta assertions.
    Full generated-case validation still belongs in validate_generated_api_cases,
    which may drop out-of-schema or unsafe generated requests before execution.
    """
    schema_endpoints = [
        endpoint for endpoint in (parsed_api_schema or []) if isinstance(endpoint, dict)
    ]
    diagnostics: list[dict[str, Any]] = []
    sanitized_cases: list[dict[str, Any]] = []
    for case in api_cases:
        if not isinstance(case, dict):
            continue
        endpoint = _endpoint_match(case, schema_endpoints) if schema_endpoints else None
        sanitized, assertion_diagnostics = _sanitize_assertions(
            case,
            endpoint,
            objective=objective,
            downgrade_ungrounded_jsonpath=downgrade_ungrounded_jsonpath,
        )
        diagnostics.extend(assertion_diagnostics)
        sanitized_cases.append(sanitized)
    return sanitized_cases, diagnostics


def documented_api_scope_text(
    parsed_api_schema: list[dict[str, Any]] | None,
    *,
    execution_policy: str = "safe_read_only",
    limit: int = 20,
) -> str:
    endpoints = [
        endpoint for endpoint in (parsed_api_schema or []) if isinstance(endpoint, dict)
    ]
    if not endpoints:
        return "No OpenAPI endpoint scope is available; do not invent paths."

    allowed: list[str] = []
    blocked: list[str] = []
    for endpoint in endpoints:
        method = _normalize_method(endpoint.get("method"))
        path = _normalize_path(endpoint.get("path"))
        label = f"{method} {path}"
        if execution_policy != "write_allowed" and method in WRITE_API_METHODS:
            blocked.append(label)
        else:
            allowed.append(label)

    allowed_text = ", ".join(allowed[:limit]) if allowed else "none"
    if len(allowed) > limit:
        allowed_text += f", ... (+{len(allowed) - limit} more)"
    blocked_text = ", ".join(blocked[:limit]) if blocked else "none"
    if len(blocked) > limit:
        blocked_text += f", ... (+{len(blocked) - limit} more)"
    return (
        f"Allowed documented endpoints for this API replan: {allowed_text}. "
        f"Policy-blocked mutation endpoints: {blocked_text}."
    )

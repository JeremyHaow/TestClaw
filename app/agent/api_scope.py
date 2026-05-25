from __future__ import annotations

import re
from copy import deepcopy
from typing import Any
from urllib.parse import urlsplit

SAFE_API_METHODS = {"GET", "HEAD", "OPTIONS"}
WRITE_API_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


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


def _json_path_grounded_in_schema(path_expr: str, schema: dict[str, Any] | None) -> bool:
    if not isinstance(schema, dict):
        return False
    path = str(path_expr or "").strip()
    if path in {"", "$"}:
        return True
    if path.startswith("$"):
        path = path[1:]
    path = path.lstrip(".")
    if not path:
        return True

    current = schema
    for raw_part in path.split("."):
        part = raw_part.split("[", 1)[0]
        if not part:
            continue
        if current.get("type") == "array" and isinstance(current.get("items"), dict):
            current = current["items"]
        child = _response_schema_property(current, part)
        if child is None:
            return False
        current = child
    return True


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


def _sanitize_assertions(
    case: dict[str, Any],
    endpoint: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    sanitized = deepcopy(case)
    diagnostics: list[dict[str, Any]] = []
    tmpl = _request_template_from_case(sanitized)
    response_schema = (
        sanitized.get("response_schema")
        or tmpl.get("response_schema")
        or (endpoint or {}).get("response_schema")
    )
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
            if _json_path_grounded_in_schema(str(path_expr), response_schema):
                assertion["type"] = "json_path"
                next_assertions.append(assertion)
                continue
            assertion["type"] = "json_path"
            assertion["blocking"] = False
            assertion["advisory"] = True
            assertion["source"] = "agent_scope_guard"
            diagnostics.append(
                _diagnostic(
                    kind="unsupported_api_assertion",
                    action="downgraded",
                    case=sanitized,
                    reason=(
                        "Generated JSON-path assertion was not grounded in the OpenAPI "
                        "response schema; it was kept as advisory evidence only."
                    ),
                    assertion=assertion,
                )
            )
            next_assertions.append(assertion)
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

        sanitized, assertion_diagnostics = _sanitize_assertions(case, endpoint)
        diagnostics.extend(assertion_diagnostics)
        valid_cases.append(sanitized)

    return valid_cases, diagnostics


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

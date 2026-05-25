import json
import logging
import re
import time
from copy import deepcopy
from urllib.parse import urljoin, urlsplit

import httpx
from openapi_schema_validator import validate

from app.agent.api_scope import (
    ALL_SAFE_GET_COVERAGE_GOAL,
    ALL_SAFE_GET_COVERAGE_SOURCE,
    safe_schema_method_endpoints,
    sanitize_api_case_assertions,
    validate_generated_api_cases,
)
from app.agent.action_runtime import (
    find_agent_action,
    record_agent_action_observation,
    validate_and_record_agent_action_plan,
)
from app.agent.progress import persist_progress
from app.agent.state import AgentState
from app.agent.strategy import (
    STRATEGY_SCHEMA_SOURCE,
    fallback_agent_strategy_decision,
    normalize_agent_strategy_decision,
    strategy_requests_all_safe_coverage,
    strategy_requests_schema_endpoint_selection,
    strategy_selected_schema_endpoints,
    strategy_summary,
)
from app.agent.tool_registry import install_tool_context, record_tool_call, summarize_tool_calls
from app.config import settings
from app.core.redaction import (
    REDACTED_VALUE,
    is_sensitive_header,
    redact_sensitive_data,
    redact_sensitive_headers,
    sanitize_persisted_text,
)
from app.services.api_auth import coerce_auth_config, has_auth_like_header, resolve_auto_auth_headers
from app.tools.mock_data import generate_mock_json_body, summarize_mock_body

logger = logging.getLogger(__name__)

SAFE_API_METHODS = {"GET", "HEAD", "OPTIONS"}
WRITE_API_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
DEFAULT_API_EXECUTION_POLICY = "safe_read_only"
API_EXECUTION_POLICIES = {
    "safe_read_only",
    "safe_with_auth",
    "write_allowed",
}
BINARY_RESPONSE_PREVIEW = "Binary or non-text response omitted from execution log"
SAFE_WRITE_BLOCK_SKIP_TYPE = "safe_write_gate_blocked"
SAFE_WRITE_BLOCK_REASON = (
    "write_allowed 策略仍需通过安全写入闸门；当前请求看起来会修改业务数据，"
    "且没有被识别为登录、验证码、导出/下载或鉴权负向探测，因此未执行。"
)
_NON_MUTATING_WRITE_MARKERS = (
    "login",
    "signin",
    "token",
    "captcha",
    "export",
    "download",
    "登录",
    "验证码",
    "导出",
    "下载",
)
_HIGH_RISK_WRITE_MARKERS = (
    "delete",
    "remove",
    "clean",
    "force",
    "reset",
    "password",
    "passwd",
    "role",
    "permission",
    "grant",
    "authorize",
    "authuser",
    "cancelall",
    "shipment",
    "warehousing",
    "approve",
    "audit",
    "pay",
    "refund",
    "删除",
    "清理",
    "强退",
    "重置",
    "密码",
    "角色",
    "权限",
    "授权",
    "出库",
    "入库",
    "审核",
    "支付",
    "退款",
)


def _max_executed_requests() -> int | None:
    try:
        value = int(getattr(settings, "API_MAX_EXECUTED_REQUESTS", 120) or 0)
    except (TypeError, ValueError):
        value = 120
    return value if value > 0 else None


def _response_content_type(response) -> str:
    headers = getattr(response, "headers", {}) or {}
    try:
        return str(headers.get("content-type") or headers.get("Content-Type") or "")
    except AttributeError:
        return ""


def _is_text_content_type(content_type: str | None) -> bool:
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    if not media_type:
        return True
    return (
        media_type.startswith("text/")
        or "json" in media_type
        or media_type.endswith("+xml")
        or media_type
        in {
            "application/xml",
            "application/javascript",
            "application/x-www-form-urlencoded",
            "application/yaml",
            "application/x-yaml",
        }
    )


def _response_byte_count(response, fallback_text: str = "") -> int:
    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        return len(content)
    return len(fallback_text.encode("utf-8", errors="replace"))


def _safe_text_preview(text: str, limit: int = 500) -> str:
    return sanitize_persisted_text(text)[:limit]


def _response_payload(response):
    content_type = _response_content_type(response)
    if _is_json_content_type(content_type):
        try:
            return response.json()
        except Exception:
            pass

    text = str(getattr(response, "text", "") or "")
    if _is_text_content_type(content_type):
        return _safe_text_preview(text)

    return {
        "content_type": content_type or "application/octet-stream",
        "byte_count": _response_byte_count(response, text),
        "preview": BINARY_RESPONSE_PREVIEW,
    }


def _stored_response_body(payload):
    safe_payload = redact_sensitive_data(payload)
    if isinstance(safe_payload, (dict, list)):
        return safe_payload
    return _safe_text_preview(str(safe_payload))


def _origin_for_url(url: str) -> str:
    parsed = urlsplit(str(url or ""))
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
    return ""


def _environment_block_scope(req: dict) -> tuple[str, str]:
    return (_origin_for_url(str(req.get("url") or "")), str(req.get("method") or "GET").upper())


def _environment_block_reason(method: str, origin: str) -> str:
    target = f"{origin} " if origin else ""
    return f"当前测试环境或上游网关不允许 {target}{method} 请求，后续同类写入请求已跳过。"


def _parse_status(val, default: int | None = None) -> int | None:
    """Parse a single response status without silently treating unknown text as 200."""
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _parse_status_values(expected_status) -> set[int]:
    if isinstance(expected_status, (list, tuple, set)):
        values: set[int] = set()
        for value in expected_status:
            values.update(_parse_status_values(value))
        return values

    if isinstance(expected_status, str):
        text = expected_status.strip()
        lowered = text.lower()
        if lowered.startswith("not_equals:"):
            return set()
        if lowered.startswith("one_of:"):
            return {int(value) for value in re.findall(r"\b\d{3}\b", text)}

    parsed = _parse_status(expected_status)
    return {parsed} if parsed is not None else set()


def _parse_status_not_equals_values(expected_status) -> set[int]:
    if not isinstance(expected_status, str):
        return set()
    text = expected_status.strip()
    if not text.lower().startswith("not_equals:"):
        return set()
    return {int(value) for value in re.findall(r"\b\d{3}\b", text)}


def _normalize_api_execution_policy(value: str | None) -> str:
    text = (value or DEFAULT_API_EXECUTION_POLICY).strip().lower()
    return text if text in API_EXECUTION_POLICIES else DEFAULT_API_EXECUTION_POLICY


def _policy_allows_write(policy: str) -> bool:
    return policy == "write_allowed"


def _has_usable_auth_headers(headers: dict | None) -> bool:
    if not isinstance(headers, dict):
        return False
    return any(str(value).strip() for value in headers.values() if value is not None)


def _is_endpoint_auth_required(endpoint: dict) -> bool:
    return bool(endpoint.get("auth_required"))


def _request_descriptor_text(
    *,
    method: str,
    path_or_url: str,
    label: str | None = None,
    endpoint: dict | None = None,
    case: dict | None = None,
) -> str:
    parts: list[str] = [method, path_or_url, label or ""]
    for source in (endpoint or {}, case or {}):
        for key in ("path", "summary", "description", "operationId", "title", "category"):
            value = source.get(key)
            if value:
                parts.append(str(value))
        tags = source.get("tags")
        if isinstance(tags, list):
            parts.extend(str(tag) for tag in tags)
    return " ".join(parts).lower()


def _is_non_mutating_write_descriptor(text: str) -> bool:
    return any(marker.lower() in text for marker in _NON_MUTATING_WRITE_MARKERS)


def _is_high_risk_write_descriptor(text: str) -> bool:
    return any(marker.lower() in text for marker in _HIGH_RISK_WRITE_MARKERS)


def _safe_write_skip_reason(
    *,
    method: str,
    path_or_url: str,
    label: str | None = None,
    endpoint: dict | None = None,
    case: dict | None = None,
    category: str | None = None,
    expected_status=None,
) -> str | None:
    method = str(method or "GET").upper()
    if method not in WRITE_API_METHODS:
        return None

    descriptor = _request_descriptor_text(
        method=method,
        path_or_url=path_or_url,
        label=label,
        endpoint=endpoint,
        case=case,
    )
    if method == "DELETE":
        return SAFE_WRITE_BLOCK_REASON

    if (
        str(category or "").upper() == "AUTH"
        and _is_endpoint_auth_required(endpoint or {})
        and (_parse_status_values(expected_status) & {401, 403})
    ):
        return None

    if _is_non_mutating_write_descriptor(descriptor):
        return None

    if _is_high_risk_write_descriptor(descriptor):
        return SAFE_WRITE_BLOCK_REASON

    return SAFE_WRITE_BLOCK_REASON


def _make_skipped_result(req: dict, reason: str) -> dict:
    result = {
        "label": req.get("label", f"{req.get('method', 'GET')} {req.get('url', '')}"),
        "method": req.get("method", "GET"),
        "url": req.get("url", ""),
        "status_code": None,
        "elapsed_ms": 0,
        "body": None,
        "request_headers": redact_sensitive_headers(req.get("headers", {})),
        "request_body": redact_sensitive_data(req.get("body")),
        "request_body_source": req.get("request_body_source"),
        "passed": None,
        "skipped": True,
        "skip_reason": reason,
        "category": req.get("category", "SKIPPED"),
        "assertion_results": [],
    }
    if req.get("skip_type"):
        result["skip_type"] = req.get("skip_type")
    if req.get("failure_type"):
        result["failure_type"] = req.get("failure_type")
    return result


def _make_environment_skipped_result(
    req: dict,
    reason: str,
    *,
    status_code: int | None = None,
    elapsed_ms: float = 0,
    body=None,
) -> dict:
    result = _make_skipped_result(req, reason)
    result.update(
        {
            "status_code": status_code,
            "elapsed_ms": elapsed_ms,
            "body": body,
            "skip_type": "environment_not_executable",
            "failure_type": "environment_not_executable",
            "environment_scope": {
                "origin": _environment_block_scope(req)[0],
                "method": _environment_block_scope(req)[1],
            },
        }
    )
    if status_code is not None:
        result["http_executed"] = True
    return result


def _expected_status_values(expected_status) -> set[int]:
    return _parse_status_values(expected_status)


def _is_auth_negative_probe(req: dict) -> bool:
    if str(req.get("category") or "").upper() != "AUTH":
        return False
    return bool(_expected_status_values(req.get("expected_status", 200)) & {401, 403})


def _strip_auth_like_headers(headers: dict | None) -> dict:
    if not isinstance(headers, dict):
        return {}
    return {
        key: value
        for key, value in headers.items()
        if not is_sensitive_header(str(key))
    }


def _matching_header_key(headers: dict, name: str) -> object | None:
    normalized = str(name).strip().lower()
    for key in headers:
        if str(key).strip().lower() == normalized:
            return key
    return None


def _is_redacted_header_placeholder(value) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return False
    lowered = text.lower()
    if REDACTED_VALUE.lower() in lowered or "redacted" in lowered:
        return True
    without_scheme = re.sub(r"(?i)^(bearer|basic)\s+", "", text).strip()
    return bool(re.fullmatch(r"\*{2,}.*", without_scheme))


def _merge_request_headers(default_headers: dict | None, template_headers: dict | None) -> dict:
    merged = dict(default_headers or {})
    if isinstance(template_headers, dict):
        for key, value in template_headers.items():
            key_text = str(key).strip()
            if not key_text or value is None or _is_redacted_header_placeholder(value):
                continue
            existing_key = _matching_header_key(merged, key_text)
            if existing_key is not None and (
                is_sensitive_header(str(existing_key)) or is_sensitive_header(key_text)
            ):
                continue
            if existing_key is not None:
                del merged[existing_key]
            merged[key] = value
    return merged


def _auth_negative_success_advisory(req: dict, resp_status: int, payload) -> str | None:
    if not _is_auth_negative_probe(req):
        return None
    if _is_auth_failure(resp_status, payload):
        return None
    if 200 <= resp_status < 300:
        return "鉴权负向探测移除鉴权后仍返回成功状态，已作为安全策略提醒记录，不计入主通过率失败。"
    return None


def _mark_assertions_advisory(assertion_results: list[dict], reason: str) -> None:
    for assertion_result in assertion_results:
        assertion_result["passed"] = None
        assertion_result["skipped"] = True
        assertion_result["advisory"] = True
        assertion_result["reason"] = reason


def _resolve_path_params(url: str, endpoint: dict) -> str:
    """Replace {param} placeholders in URL with example values from schema."""
    import re
    path_params = endpoint.get("path_params", [])
    if not path_params:
        # Fallback: replace any {param} with a generic value
        return re.sub(r"\{(\w+)\}", "1", url)
    for param in path_params:
        name = param.get("name", "")
        if not name:
            continue
        # Use example value if available, otherwise generate from type
        example = param.get("example")
        if example is None:
            schema_type = param.get("schema", {}).get("type") or param.get("type", "string")
            if schema_type in ("integer", "int"):
                example = 1
            elif schema_type in ("number", "float", "double"):
                example = 1.0
            elif schema_type == "boolean":
                example = True
            else:
                example = "1"
        url = url.replace(f"{{{name}}}", str(example))
    # Catch any remaining unresolved params
    url = re.sub(r"\{(\w+)\}", "1", url)
    return url


def _extract_query_params(endpoint: dict) -> dict:
    """Extract required query params with example values from endpoint schema."""
    params = {}
    for qp in endpoint.get("query_params") or []:
        if not isinstance(qp, dict):
            continue
        name = qp.get("name", "")
        if not name:
            continue
        # Use example value, then enum first value, then type-based default
        val = qp.get("example")
        if val is None:
            enum_vals = qp.get("enum") or qp.get("schema", {}).get("enum")
            if enum_vals:
                val = enum_vals[0]
            else:
                qtype = qp.get("type") or qp.get("schema", {}).get("type", "string")
                if qtype == "integer":
                    val = 1
                elif qtype == "number":
                    val = 1.0
                elif qtype == "boolean":
                    val = True
                elif qtype == "array":
                    val = ["test"]
                else:
                    val = "test"
        params[name] = val
    return params


def _is_json_content_type(content_type: str | None) -> bool:
    text = (content_type or "application/json").lower()
    return "json" in text or text in {"", "*/*"}


def _body_required_fields(endpoint: dict) -> list[str]:
    schema = endpoint.get("request_body_schema")
    if isinstance(schema, dict) and isinstance(schema.get("required"), list):
        return [str(field) for field in schema["required"] if isinstance(field, str)]
    required = endpoint.get("required_fields") or []
    if not isinstance(required, list):
        return []
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if isinstance(properties, dict):
        return [str(field) for field in required if str(field) in properties]
    return [str(field) for field in required]


def _request_body_from_schema(
    endpoint: dict,
    *,
    method: str,
    path: str,
    content_type: str,
) -> tuple[object, dict | None]:
    schema = endpoint.get("request_body_schema")
    example_request = endpoint.get("example_request")
    if not isinstance(schema, dict) or not _is_json_content_type(content_type):
        return example_request, None

    required_fields = _body_required_fields(endpoint)
    generated = generate_mock_json_body(
        schema,
        required_fields=required_fields,
        field_context=f"{method} {path}",
    )
    if generated == {} and example_request:
        return example_request, None
    if generated is None:
        return example_request, None

    return generated, {
        "source": "faker_json_schema",
        "method": method,
        "path": path,
        "content_type": content_type,
        "required_fields": required_fields,
        "summary": summarize_mock_body(generated),
    }


def _record_mock_body_generation(state: AgentState, req: dict) -> None:
    generation = req.get("mock_body_generation")
    if not isinstance(generation, dict):
        return
    record_tool_call(
        state,
        tool_name="api.generate_mock_json_body",
        layer="api",
        status="success",
        input_summary={
            "method": generation.get("method"),
            "path": generation.get("path"),
            "content_type": generation.get("content_type"),
            "required_fields": generation.get("required_fields", []),
        },
        output_summary=generation.get("summary", {}),
    )


def _build_request_url(base_url: str, path_or_url: str) -> str:
    path_or_url = (path_or_url or "").strip()
    if path_or_url.startswith(("http://", "https://")):
        return path_or_url
    base_url = (base_url or "").strip().rstrip("/")
    if not base_url:
        return ""
    if not path_or_url:
        return base_url
    if path_or_url.startswith("/"):
        return f"{base_url}{path_or_url}"
    return urljoin(f"{base_url}/", path_or_url)


def _request_template_from_case(case: dict) -> dict:
    direct = case.get("request_template")
    if isinstance(direct, dict):
        return direct

    test_data = case.get("test_data")
    if not isinstance(test_data, dict):
        return {}

    nested = test_data.get("request_template")
    if isinstance(nested, dict):
        return nested

    if any(key in test_data for key in ("method", "url", "path", "base_url")):
        return test_data

    return {}


def _payload_status_code(payload) -> int | None:
    if not isinstance(payload, dict):
        return None
    for key in ("code", "status", "status_code"):
        value = payload.get(key)
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _is_auth_failure(resp_status: int, payload) -> bool:
    payload_status = _payload_status_code(payload)
    return resp_status in {401, 403} or payload_status in {401, 403}


def _business_error_message(payload) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("msg", "message", "error", "error_description", "detail", "reason"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        return str(errors[0]).strip()
    if isinstance(errors, dict) and errors:
        first_value = next(iter(errors.values()))
        return str(first_value).strip()
    return ""


def _payload_success_false(payload) -> bool:
    if not isinstance(payload, dict) or "success" not in payload:
        return False
    value = payload.get("success")
    if isinstance(value, bool):
        return value is False
    if isinstance(value, str):
        return value.strip().lower() in {"false", "0", "no", "failed", "fail"}
    if isinstance(value, (int, float)):
        return value == 0
    return False


def _param_validation_business_rejection(req: dict, resp_status: int, payload) -> str | None:
    if str(req.get("category") or "").upper() != "PARAM_VALIDATION":
        return None
    if not (200 <= resp_status < 300):
        return None
    if _is_auth_failure(resp_status, payload):
        return None
    message = _business_error_message(payload)
    if not message:
        return None
    payload_status = _payload_status_code(payload)
    has_error_status = payload_status is not None and payload_status >= 400
    if not (has_error_status or _payload_success_false(payload)):
        return None
    if payload_status is not None and payload_status >= 500:
        return "无效参数已通过业务错误信封拒绝；业务 code/status 为 5xx，建议后端评估是否改为明确的 4xx 校验错误。"
    return "无效参数已通过业务错误信封拒绝，按负向参数校验通过处理。"


def _mark_business_rejection_assertions(assertion_results: list[dict], warning: str) -> None:
    for assertion_result in assertion_results:
        if assertion_result.get("type") != "status_code":
            continue
        if assertion_result.get("passed") is False:
            assertion_result["passed"] = True
            assertion_result["accepted_error_envelope"] = True
            assertion_result["warning"] = warning


def _status_matches(expected_status, http_status: int, payload) -> bool:
    payload_status = _payload_status_code(payload)
    actual_candidates = [http_status]
    if payload_status is not None:
        actual_candidates.append(payload_status)

    not_equals_values = _parse_status_not_equals_values(expected_status)
    if not_equals_values:
        return all(value not in not_equals_values for value in actual_candidates)

    expected_values = _parse_status_values(expected_status)
    if not expected_values:
        return False

    if not isinstance(expected_status, (list, tuple, set)) and expected_values == {200}:
        if 200 <= http_status < 300:
            return payload_status is None or 200 <= payload_status < 300
        return False
    return any(value in expected_values for value in actual_candidates)


_JSON_PATH_TOKEN_RE = re.compile(r"([A-Za-z0-9_\-]+)|\[(\d+)\]")
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_.\-]+)\s*\}\}")


def _json_path_get(payload, path_expr: str):
    path_expr = (path_expr or "").strip()
    if not path_expr:
        return payload
    if path_expr.startswith("$"):
        path_expr = path_expr[1:]
    path_expr = path_expr.lstrip(".")
    if not path_expr:
        return payload

    current = payload
    for part in path_expr.split("."):
        if not part:
            continue
        matches = list(_JSON_PATH_TOKEN_RE.finditer(part))
        if not matches:
            return None
        for match in matches:
            key, index = match.groups()
            if key is not None:
                if not isinstance(current, dict):
                    return None
                current = current.get(key)
            else:
                if not isinstance(current, list):
                    return None
                idx = int(index)
                if idx >= len(current):
                    return None
                current = current[idx]
            if current is None:
                return None
    return current


def _substitute_context(value, context: dict[str, object]):
    if isinstance(value, str):
        return _PLACEHOLDER_RE.sub(lambda match: str(context.get(match.group(1), "")), value)
    if isinstance(value, list):
        return [_substitute_context(item, context) for item in value]
    if isinstance(value, dict):
        return {key: _substitute_context(item, context) for key, item in value.items()}
    return value


def _extract_specs(value) -> list[dict]:
    if not value:
        return []
    if isinstance(value, dict):
        return [
            {"name": name, "path": path}
            for name, path in value.items()
            if isinstance(name, str) and isinstance(path, str)
        ]
    if isinstance(value, list):
        specs = []
        for item in value:
            if isinstance(item, dict) and item.get("name") and item.get("path"):
                specs.append({"name": str(item["name"]), "path": str(item["path"])})
        return specs
    return []


def _missing_dependencies(req: dict, context: dict[str, object]) -> list[str]:
    depends_on = req.get("depends_on") or []
    if isinstance(depends_on, str):
        depends_on = [depends_on]
    return [str(name) for name in depends_on if str(name) not in context]


def _evaluate_status_assertion(assertion: dict, resp_status: int, payload) -> dict:
    expected = assertion.get("expected", 200)
    passed = _status_matches(expected, resp_status, payload)
    payload_status = _payload_status_code(payload)
    actual = [resp_status] if payload_status is None else [resp_status, payload_status]
    result = {
        "type": "status_code",
        "expected": expected,
        "actual": actual,
        "passed": passed,
        "blocking": assertion.get("blocking", True),
    }
    if assertion.get("advisory"):
        result["advisory"] = True
    if assertion.get("source"):
        result["source"] = assertion.get("source")
    return result


def _evaluate_json_path_assertion(assertion: dict, payload) -> dict:
    path_expr = assertion.get("path") or assertion.get("json_path") or "$"
    actual = _json_path_get(payload, path_expr) if isinstance(payload, (dict, list)) else None
    operator = assertion.get("operator") or assertion.get("op")
    expected = assertion.get("expected")
    if expected == "not_null" and not operator:
        operator = "not_null"
    operator = operator or "equals"

    if operator in {"exists", "present"}:
        passed = actual is not None
    elif operator in {"not_null", "non_null"}:
        passed = actual is not None
    elif operator == "contains":
        if isinstance(actual, (list, tuple, set)):
            passed = expected in actual
        else:
            passed = str(expected) in str(actual)
    elif operator in {"not_equals", "!="}:
        passed = actual != expected
    else:
        passed = actual == expected

    result = {
        "type": "json_path",
        "path": path_expr,
        "operator": operator,
        "expected": expected,
        "actual": actual,
        "passed": passed,
        "blocking": assertion.get("blocking", True),
    }
    if assertion.get("advisory"):
        result["advisory"] = True
    if assertion.get("source"):
        result["source"] = assertion.get("source")
    return result


def _json_value_type(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    return type(value).__name__


def _evaluate_json_type_assertion(assertion: dict, payload) -> dict:
    path_expr = assertion.get("path") or "$"
    actual = _json_path_get(payload, path_expr) if isinstance(payload, (dict, list)) else payload
    expected = str(assertion.get("expected") or "").strip().lower()
    actual_type = _json_value_type(actual)
    passed = actual_type == expected or (expected == "number" and actual_type == "integer")
    result = {
        "type": "json_type",
        "path": path_expr,
        "expected": expected,
        "actual": actual_type,
        "passed": passed,
        "blocking": assertion.get("blocking", True),
        "source": assertion.get("source"),
    }
    if assertion.get("advisory"):
        result["advisory"] = True
    return result


def _evaluate_schema_assertion(assertion: dict, payload, fallback_schema: dict | None = None) -> dict:
    schema = assertion.get("schema") or fallback_schema
    blocking = assertion.get("blocking", True)
    if not schema:
        return {
            "type": "schema",
            "passed": None,
            "blocking": False,
            "skipped": True,
            "reason": "No schema available",
        }
    if not isinstance(payload, (dict, list)):
        return {
            "type": "schema",
            "passed": False,
            "blocking": blocking,
            "error": "Response body is not JSON",
        }
    try:
        validate(payload, schema)
        return {"type": "schema", "passed": True, "blocking": blocking}
    except Exception as exc:
        return {
            "type": "schema",
            "passed": False,
            "blocking": blocking,
            "error": str(exc)[:500],
        }


def _evaluate_body_contains_assertion(assertion: dict, payload) -> dict:
    expected = assertion.get("expected") or assertion.get("text")
    body_text = json.dumps(payload, ensure_ascii=False, default=str) if isinstance(payload, (dict, list)) else str(payload)
    return {
        "type": "body_contains",
        "expected": expected,
        "passed": str(expected) in body_text,
        "blocking": assertion.get("blocking", True),
    }


def _evaluate_assertions(req: dict, resp_status: int, payload) -> list[dict]:
    assertions = list(req.get("assertions") or [])
    if not assertions:
        assertions.append({"type": "status_code", "expected": req.get("expected_status", 200)})
    elif not any(assertion.get("type") == "status_code" for assertion in assertions):
        assertions.insert(0, {"type": "status_code", "expected": req.get("expected_status", 200)})

    response_schema = req.get("response_schema")
    if response_schema and req.get("auto_schema_assertion"):
        assertions.append(
            {
                "type": "schema",
                "schema": response_schema,
                "blocking": req.get("schema_assertion_mode") == "blocking",
                "source": "openapi_response_schema",
            }
        )

    results = []
    for assertion in assertions:
        atype = str(assertion.get("type") or "").lower()
        if atype == "status_code":
            results.append(_evaluate_status_assertion(assertion, resp_status, payload))
        elif atype in {"json_path", "jsonpath"}:
            results.append(_evaluate_json_path_assertion(assertion, payload))
        elif atype in {"json_type", "type"}:
            results.append(_evaluate_json_type_assertion(assertion, payload))
        elif atype in {"schema", "schema_valid", "json_schema"}:
            results.append(_evaluate_schema_assertion(assertion, payload, response_schema))
        elif atype in {"body_contains", "contains"}:
            results.append(_evaluate_body_contains_assertion(assertion, payload))
    return results


def _assertions_pass(assertion_results: list[dict], fallback_passed: bool) -> bool:
    blocking = [
        result for result in assertion_results
        if result.get("blocking", True) and result.get("passed") is not None and not result.get("skipped")
    ]
    if not blocking:
        return fallback_passed
    return all(result.get("passed") is True for result in blocking)


def _classify_api_failure(req: dict, resp_status: int, payload, assertion_results: list[dict]) -> dict | None:
    payload_status = _payload_status_code(payload)
    if _is_auth_failure(resp_status, payload):
        return {
            "failure_type": "auth_failure",
            "failure_reason": "请求返回未授权状态，可能是 Token 失效或鉴权头不完整。",
        }

    category = str(req.get("category") or "").upper()
    if category == "PARAM_VALIDATION":
        if resp_status >= 500 or (payload_status is not None and payload_status >= 500):
            return {
                "failure_type": "backend_validation_contract",
                "failure_reason": "无效参数场景返回了服务端错误状态，应由后端校验为明确的 4xx 参数错误。",
            }
        if 200 <= resp_status < 300 and payload_status is not None and payload_status >= 400:
            return {
                "failure_type": "backend_validation_contract",
                "failure_reason": "无效参数场景使用 HTTP 200 承载错误响应，和预期的 4xx 参数校验契约不一致。",
            }
        if 200 <= resp_status < 300:
            return {
                "failure_type": "backend_validation_contract",
                "failure_reason": "无效参数场景返回成功 HTTP 状态，后端未按契约拒绝非法输入。",
            }
        return {
            "failure_type": "validation_contract",
            "failure_reason": "参数校验响应与预期状态不一致。",
        }

    if resp_status >= 500 or (payload_status is not None and payload_status >= 500):
        return {
            "failure_type": "backend_error",
            "failure_reason": "请求触发了服务端错误状态。",
        }

    if any(
        result.get("type") == "schema"
        and result.get("blocking", True)
        and result.get("passed") is False
        for result in assertion_results
    ):
        return {
            "failure_type": "schema_contract",
            "failure_reason": "响应 JSON 结构不符合 OpenAPI Schema。",
        }

    return {
        "failure_type": "api_assertion",
        "failure_reason": "响应未满足本次 API 断言。",
    }


def _record_http_request_call(
    state: AgentState,
    req: dict,
    resp_status: int,
    attempts: int,
    elapsed: float,
    *,
    auth_refresh_retry: bool = False,
) -> None:
    record_tool_call(
        state,
        tool_name="api.http_request",
        layer="api",
        status="success" if resp_status < 500 else "failed",
        input_summary={
            "method": req.get("method"),
            "url": req.get("url"),
            "params": bool(req.get("query_params")),
            "body": req.get("body") is not None,
            "auth_refresh_retry": auth_refresh_retry,
        },
        output_summary={
            "status_code": resp_status,
            "attempts": attempts,
        },
        elapsed_ms=elapsed,
    )


async def _request_with_retry(
    client: httpx.AsyncClient,
    req: dict,
    retry_count: int,
    request_budget: int | None = None,
):
    attempts = max(1, retry_count + 1)
    if request_budget is not None:
        if request_budget <= 0:
            return None, 0, RuntimeError("HTTP execution budget exhausted")
        attempts = min(attempts, request_budget)
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = await client.request(
                req["method"],
                req["url"],
                headers=req.get("headers") or None,
                json=req.get("body"),
                params=req.get("query_params"),
            )
            if response.status_code < 500 or attempt == attempts:
                return response, attempt, None
        except Exception as exc:
            last_exc = exc
            if attempt == attempts:
                return None, attempt, exc
    return None, attempts, last_exc


def _can_refresh_auth(state: AgentState, req: dict) -> bool:
    config = coerce_auth_config(state.get("auth_config"))
    if not config.get("enabled"):
        return False
    if str(req.get("category") or "").upper() == "AUTH":
        return False
    return has_auth_like_header(req.get("headers")) or has_auth_like_header(state.get("auth_headers"))


async def _refresh_auth_for_request(
    state: AgentState,
    req: dict,
    endpoints: list[dict] | None,
) -> dict[str, str] | None:
    config = coerce_auth_config(state.get("auth_config"))
    resolution = await resolve_auto_auth_headers(
        config,
        source=str(state.get("source_input") or ""),
        input_type=str(state.get("input_type") or "url"),
        target_url=str(state.get("base_url_override") or state.get("target_url") or ""),
        endpoints=endpoints,
    )
    record_tool_call(
        state,
        tool_name="api.auth_refresh",
        layer="api",
        status="success" if resolution.ok else "failed",
        input_summary={
            "method": req.get("method"),
            "url": req.get("url"),
            "reason": "401/403",
        },
        output_summary={
            "refreshed": resolution.ok,
            "header_name": resolution.header_name,
            "detail": resolution.detail,
        },
    )
    if not resolution.ok:
        return None

    merged_headers = dict(state.get("auth_headers") or {})
    merged_headers.update(resolution.headers)
    state["auth_headers"] = merged_headers
    return resolution.headers


def _build_case_test_requests(
    api_cases: list[dict],
    base_url: str,
    target_url: str,
    auth_headers: dict | None,
    *,
    write_allowed: bool,
) -> list[dict]:
    requests = []
    default_headers = auth_headers or {}
    for case in api_cases:
        if not isinstance(case, dict):
            continue
        tmpl = _request_template_from_case(case)
        if not tmpl:
            continue
        method = str(tmpl.get("method", "GET")).upper()
        url = tmpl.get("url") or tmpl.get("path") or tmpl.get("endpoint") or ""
        url = _build_request_url(base_url, url)
        url = _resolve_path_params(url, {"path_params": tmpl.get("path_params", [])})
        if not url:
            continue
        if method in WRITE_API_METHODS and not write_allowed:
            requests.append({
                "label": case.get("title", f"SKIPPED_WRITE {method} {url}"),
                "method": method,
                "url": url,
                "headers": {},
                "body": None,
                "expected_status": None,
                "category": "SKIPPED",
                "skip_reason": "当前策略为安全只读，未执行会创建、修改或删除数据的请求",
            })
            continue
        if method in WRITE_API_METHODS:
            safe_write_reason = _safe_write_skip_reason(
                method=method,
                path_or_url=tmpl.get("path") or tmpl.get("url") or url,
                label=case.get("title"),
                case=case,
                category=case.get("category"),
                expected_status=tmpl.get("expected_status", case.get("expected_status", 200)),
            )
            if safe_write_reason:
                requests.append({
                    "label": case.get("title", f"BLOCKED_WRITE {method} {url}"),
                    "method": method,
                    "url": url,
                    "headers": {},
                    "body": None,
                    "expected_status": None,
                    "category": "SKIPPED",
                    "skip_reason": safe_write_reason,
                    "skip_type": SAFE_WRITE_BLOCK_SKIP_TYPE,
                    "failure_type": SAFE_WRITE_BLOCK_SKIP_TYPE,
                })
                continue
        content_type = (
            tmpl.get("content_type")
            or tmpl.get("request_body_content_type")
            or case.get("request_body_content_type")
            or "application/json"
        )
        body = tmpl.get("body", tmpl.get("json"))
        body_generation = None
        request_body_schema = tmpl.get("request_body_schema") or case.get("request_body_schema")
        if body is None and isinstance(request_body_schema, dict) and _is_json_content_type(content_type):
            body = generate_mock_json_body(
                request_body_schema,
                required_fields=case.get("required_fields") or [],
                field_context=f"{method} {tmpl.get('path') or tmpl.get('url') or ''}",
            )
            if body is not None:
                body_generation = {
                    "source": "faker_json_schema",
                    "method": method,
                    "path": tmpl.get("path") or tmpl.get("url") or "",
                    "content_type": content_type,
                    "required_fields": case.get("required_fields") or [],
                    "summary": summarize_mock_body(body),
                }
        request_headers = _merge_request_headers(default_headers, tmpl.get("headers"))
        if body is not None:
            request_headers.setdefault("Content-Type", content_type)
        expected_status = tmpl.get("expected_status", case.get("expected_status", 200))
        category = case.get("category", "SMOKE")
        request = {
            "label": case.get("title", f"{method} {url}"),
            "method": method,
            "url": url or target_url,
            "headers": request_headers,
            "body": body,
            "query_params": tmpl.get("query_params") or tmpl.get("params") or {},
            "expected_status": expected_status,
            "response_schema": tmpl.get("response_schema") or case.get("response_schema"),
            "auto_schema_assertion": bool(tmpl.get("response_schema") or case.get("response_schema")),
            "schema_assertion_mode": tmpl.get(
                "schema_assertion_mode",
                case.get("schema_assertion_mode", "blocking"),
            ),
            "depends_on": tmpl.get("depends_on") or case.get("depends_on"),
            "extract": tmpl.get("extract") or case.get("extract"),
            "category": category,
            "assertions": case.get("assertions") or tmpl.get("assertions") or [],
            "request_body_source": "faker_json_schema" if body_generation else "case_template",
        }
        if _is_auth_negative_probe(request):
            request["headers"] = _strip_auth_like_headers(request_headers)
        if body_generation:
            request["mock_body_generation"] = body_generation
        requests.append(request)
    return requests


def _has_executable_request(requests: list[dict]) -> bool:
    return any(not request.get("skip_reason") for request in requests)


def _safe_schema_subset(api_schema: list[dict], limit: int | None) -> list[dict]:
    subset = []
    for endpoint in safe_schema_method_endpoints(api_schema):
        subset.append(endpoint)
        if limit is not None and len(subset) >= limit:
            break
    return subset


def _select_requests_for_execution(
    requests: list[dict],
    execution_budget: int | None,
    *,
    source: str,
    fallback_reason: str | None = None,
    coverage_metadata: dict | None = None,
) -> tuple[list[dict], dict]:
    candidate_total = len(requests)
    if execution_budget is None or candidate_total <= execution_budget:
        selected = list(requests)
    else:
        executable = [request for request in requests if not request.get("skip_reason")]
        skipped = [request for request in requests if request.get("skip_reason")]
        selected = executable[:execution_budget]
        if len(selected) < execution_budget:
            selected.extend(skipped[: execution_budget - len(selected)])

    budget_omitted = max(candidate_total - len(selected), 0)
    metadata = {
        "source": source,
        "candidate_total": candidate_total,
        "selected_total": len(selected),
        "budget_limit": execution_budget,
        "budget_omitted": budget_omitted,
        "runtime_budget_omitted": 0,
        "omitted": budget_omitted,
        "bounded": budget_omitted > 0,
    }
    if fallback_reason:
        metadata["fallback_reason"] = fallback_reason
    if coverage_metadata:
        metadata.update(coverage_metadata)
    return selected, metadata


def _schema_endpoint_identity(request: dict) -> str | None:
    method = str(request.get("schema_method") or request.get("method") or "").upper()
    path = str(request.get("schema_path") or "").strip()
    if not method or not path:
        return None
    return f"{method} {path}"


def _all_safe_schema_coverage_metadata(
    safe_endpoints: list[dict],
    selected_requests: list[dict],
) -> dict:
    selected_endpoint_ids = {
        identity
        for request in selected_requests
        if (identity := _schema_endpoint_identity(request))
    }
    safe_endpoint_total = len(safe_endpoints)
    selected_safe_endpoint_total = len(selected_endpoint_ids)
    omitted_safe_endpoint_total = max(safe_endpoint_total - selected_safe_endpoint_total, 0)
    return {
        "coverage_goal": ALL_SAFE_GET_COVERAGE_GOAL,
        "safe_methods": sorted(SAFE_API_METHODS),
        "safe_endpoint_total": safe_endpoint_total,
        "selected_safe_endpoint_total": selected_safe_endpoint_total,
        "omitted_safe_endpoint_total": omitted_safe_endpoint_total,
        "bounded": omitted_safe_endpoint_total > 0 or len(selected_requests) < safe_endpoint_total,
    }


def _strategy_schema_coverage_metadata(
    strategy: dict,
    strategy_endpoints: list[dict],
    selected_requests: list[dict],
) -> dict:
    selected_endpoint_ids = {
        identity
        for request in selected_requests
        if (identity := _schema_endpoint_identity(request))
    }
    selected_total = len(selected_endpoint_ids)
    endpoint_total = len(strategy_endpoints)
    omitted_total = max(endpoint_total - selected_total, 0)
    selection = strategy.get("endpoint_selection") or {}
    return {
        "coverage_goal": strategy.get("coverage_scope"),
        "coverage_scope": strategy.get("coverage_scope"),
        "strategy_intent": strategy.get("intent"),
        "strategy_source": strategy.get("source"),
        "strategy_summary": strategy_summary(strategy),
        "budget_behavior": selection.get("budget_behavior"),
        "strategy_endpoint_total": endpoint_total,
        "selected_strategy_endpoint_total": selected_total,
        "omitted_strategy_endpoint_total": omitted_total,
        "strategy_coverage_completed": len(selected_requests) > 0,
        "bounded": omitted_total > 0 or len(selected_requests) < endpoint_total,
    }


def _api_execution_progress_detail(total_requests: int, request_selection: dict) -> str:
    if request_selection.get("source") == ALL_SAFE_GET_COVERAGE_SOURCE:
        detail = (
            "Executing schema-driven all-safe-GET coverage: "
            f"{request_selection.get('selected_safe_endpoint_total', total_requests)}/"
            f"{request_selection.get('safe_endpoint_total', total_requests)} safe endpoint(s), "
            f"{total_requests} selected request(s)"
        )
        if request_selection.get("omitted"):
            detail += f"; {request_selection.get('omitted')} request candidate(s) omitted by execution budget"
        if request_selection.get("omitted_safe_endpoint_total"):
            detail += f"; {request_selection.get('omitted_safe_endpoint_total')} safe endpoint(s) omitted"
        return detail

    if not request_selection.get("omitted"):
        return f"Executing {total_requests} API request(s)"
    return (
        f"Executing {total_requests} selected API request(s); "
        f"{request_selection.get('omitted')} omitted by execution budget"
    )


def _build_test_requests(
    api_schema: list[dict],
    base_url: str,
    headers: dict | None = None,
    execution_policy: str = DEFAULT_API_EXECUTION_POLICY,
) -> list[dict]:
    """Generate executable requests from parsed API schema."""
    requests = []
    default_headers = headers or {}
    policy = _normalize_api_execution_policy(execution_policy)
    auth_available = _has_usable_auth_headers(default_headers)
    write_allowed = _policy_allows_write(policy)

    for endpoint in api_schema:
        path = endpoint.get("path", "")
        method = endpoint.get("method", "GET").upper()
        path = _resolve_path_params(path, endpoint)
        full_url = _build_request_url(base_url, path)
        if not full_url:
            continue
        is_safe_method = method in SAFE_API_METHODS
        auth_required = _is_endpoint_auth_required(endpoint)

        if method in WRITE_API_METHODS and not write_allowed:
            requests.append({
                "label": f"SKIPPED_WRITE {method} {path}",
                "method": method,
                "url": full_url,
                "schema_method": method,
                "schema_path": path,
                "headers": {},
                "body": None,
                "expected_status": None,
                "category": "SKIPPED",
                "skip_reason": "当前策略为安全只读，未执行会创建、修改或删除数据的请求",
            })
            continue
        if method in WRITE_API_METHODS:
            safe_write_reason = _safe_write_skip_reason(
                method=method,
                path_or_url=path,
                label=f"{method} {path}",
                endpoint=endpoint,
                category="SMOKE",
                expected_status=_parse_status(endpoint.get("response_status", "200"), default=200),
            )
            if safe_write_reason:
                requests.append({
                    "label": f"BLOCKED_WRITE {method} {path}",
                    "method": method,
                    "url": full_url,
                    "schema_method": method,
                    "schema_path": path,
                    "headers": {},
                    "body": None,
                    "expected_status": None,
                    "category": "SKIPPED",
                    "skip_reason": safe_write_reason,
                    "skip_type": SAFE_WRITE_BLOCK_SKIP_TYPE,
                    "failure_type": SAFE_WRITE_BLOCK_SKIP_TYPE,
                })
                continue

        required_fields = _body_required_fields(endpoint)
        query_params = _extract_query_params(endpoint)
        content_type = endpoint.get("request_body_content_type") or "application/json"

        # Skip file upload endpoints entirely (can't test without real files)
        is_upload = "multipart" in content_type.lower() or "form-data" in content_type.lower()
        if is_upload:
            requests.append({
                "label": f"SKIPPED_UPLOAD {method} {path}",
                "method": method,
                "url": full_url,
                "schema_method": method,
                "schema_path": path,
                "headers": {},
                "body": None,
                "expected_status": None,
                "category": "SKIPPED",
                "skip_reason": "文件上传接口需要真实文件资产，本次未自动执行",
            })
            continue

        req_body, body_generation = _request_body_from_schema(
            endpoint,
            method=method,
            path=path,
            content_type=content_type,
        )

        if auth_required and not auth_available:
            if is_safe_method:
                requests.append({
                    "label": f"UNAUTHORIZED {method} {path}",
                    "method": method,
                    "url": full_url,
                    "schema_method": method,
                    "schema_path": path,
                    "headers": {},
                    "body": None,
                    "query_params": query_params,
                    "expected_status": [401, 403],
                    "category": "AUTH",
                })
            requests.append({
                "label": f"SKIPPED_AUTH {method} {path}",
                "method": method,
                "url": full_url,
                "schema_method": method,
                "schema_path": path,
                "headers": {},
                "body": None,
                "expected_status": None,
                "category": "SKIPPED",
                "skip_reason": "接口声明需要鉴权；未提供 Token/Header，跳过正向业务断言",
            })
            continue

        # 1. Smoke test — use example request + required query params
        smoke_headers = {**default_headers}
        if req_body is not None and not is_upload:
            smoke_headers["Content-Type"] = content_type
        smoke_request = {
            "label": f"SMOKE {method} {path}",
            "method": method,
            "url": full_url,
            "schema_method": method,
            "schema_path": path,
            "headers": smoke_headers,
            "body": req_body if not is_upload else None,
            "query_params": query_params,
            "expected_status": _parse_status(endpoint.get("response_status", "200"), default=200),
            "response_schema": endpoint.get("response_schema"),
            "auto_schema_assertion": bool(endpoint.get("response_schema")),
            "schema_assertion_mode": "advisory",
            "category": "SMOKE",
            "request_body_source": "faker_json_schema" if body_generation else "example_request",
        }
        if body_generation:
            smoke_request["mock_body_generation"] = body_generation
        requests.append(smoke_request)

        # 2. Missing required fields (POST/PUT/PATCH only)
        if (
            method in ("POST", "PUT", "PATCH")
            and required_fields
            and req_body
            and isinstance(req_body, dict)
        ):
            for field in required_fields[:3]:
                broken_body = {k: v for k, v in req_body.items() if k != field}
                requests.append({
                    "label": f"MISSING_FIELD {method} {path} (no {field})",
                    "method": method,
                    "url": full_url,
                    "schema_method": method,
                    "schema_path": path,
                    "headers": {**default_headers, "Content-Type": content_type},
                    "body": broken_body,
                    "expected_status": [400, 422],
                    "response_schema": endpoint.get("response_schema"),
                    "category": "PARAM_VALIDATION",
                })

        # 3. Empty body for POST/PUT/PATCH
        if method in ("POST", "PUT", "PATCH"):
            requests.append({
                "label": f"EMPTY_BODY {method} {path}",
                "method": method,
                "url": full_url,
                "schema_method": method,
                "schema_path": path,
                "headers": {**default_headers, "Content-Type": "application/json"},
                "body": {},
                "expected_status": [400, 422],
                "response_schema": endpoint.get("response_schema"),
                "category": "PARAM_VALIDATION",
            })

        # 4. Unauthorized test
        if auth_required:
            requests.append({
                "label": f"UNAUTHORIZED {method} {path}",
                "method": method,
                "url": full_url,
                "schema_method": method,
                "schema_path": path,
                "headers": {"Content-Type": "application/json"} if req_body is not None else {},
                "body": req_body,
                "query_params": query_params,
                "expected_status": [401, 403],
                "response_schema": endpoint.get("response_schema"),
                "category": "AUTH",
                "request_body_source": "faker_json_schema" if body_generation else "example_request",
            })

        # 5. Invalid type for string fields (skip upload endpoints)
        if (
            method in ("POST", "PUT", "PATCH")
            and req_body
            and isinstance(req_body, dict)
            and not is_upload
        ):
            bad_body = {}
            for k, v in req_body.items():
                if isinstance(v, str):
                    bad_body[k] = 12345  # wrong type
                elif isinstance(v, (int, float)):
                    bad_body[k] = "invalid_string"
                else:
                    bad_body[k] = v
            if bad_body != req_body:
                requests.append({
                    "label": f"INVALID_TYPE {method} {path}",
                    "method": method,
                    "url": full_url,
                    "schema_method": method,
                    "schema_path": path,
                    "headers": {**default_headers, "Content-Type": content_type},
                    "body": bad_body,
                    "expected_status": [400, 422],
                    "response_schema": endpoint.get("response_schema"),
                    "category": "PARAM_VALIDATION",
                })

    return requests


def _update_api_execution_state(
    state: AgentState,
    results: list[dict],
    total_requests: int,
    complete: bool,
    *,
    http_executed_count: int | None = None,
    execution_budget: int | None = None,
) -> None:
    safe_results = redact_sensitive_data(results)
    request_selection = state.get("api_request_selection") or {}
    budget_omitted_count = int(request_selection.get("omitted") or 0)
    candidate_total = int(request_selection.get("candidate_total") or total_requests)
    skipped_count = sum(1 for r in results if r.get("skipped"))
    advisory_count = sum(1 for r in results if r.get("advisory"))
    environment_skipped_count = sum(1 for r in results if r.get("skip_type") == "environment_not_executable")
    budget_skipped_count = (
        sum(1 for r in results if r.get("skip_type") == "execution_budget_exhausted")
        + budget_omitted_count
    )
    executed_count = len(results) - skipped_count
    passed_count = sum(1 for r in results if r.get("passed") is True)
    completed = len(results)
    failed_count = max(executed_count - passed_count, 0)
    pending_count = 0 if complete else max(total_requests - completed, 0)
    all_passed = complete and total_requests > 0 and failed_count == 0
    stderr = ""
    if complete and total_requests == 0:
        stderr = "No executable API requests were built"
    elif complete and failed_count:
        stderr = f"{failed_count} API test(s) failed"

    state["api_execution_result"] = {
        "total": total_requests,
        "candidate_total": candidate_total,
        "selected_total": total_requests,
        "omitted": budget_omitted_count,
        "completed": completed,
        "executed": executed_count,
        "passed": passed_count,
        "failed": failed_count,
        "skipped": skipped_count,
        "advisory": advisory_count,
        "environment_skipped": environment_skipped_count,
        "budget_skipped": budget_skipped_count,
        "pending": pending_count,
        "http_executed": http_executed_count if http_executed_count is not None else sum(1 for r in results if r.get("http_executed")),
        "execution_budget": execution_budget,
        "budget_exhausted": budget_skipped_count > 0,
        "request_selection": request_selection,
        "pass_rate": f"{round(passed_count / executed_count * 100, 1)}%" if executed_count else "0%",
        "results": safe_results,
        "all_passed": all_passed,
        "complete": complete,
    }

    state["execution_result"] = {
        "status_code": 0 if all_passed or not complete else 1,
        "stdout": json.dumps(safe_results, ensure_ascii=False, default=str)[:3000],
        "stderr": stderr,
        "trace_path": None,
        "api_results": safe_results,
    }


async def run(state: AgentState) -> AgentState:
    install_tool_context(state)
    state["agent_execution_stage"] = "api"
    api_schema = state.get("parsed_api_schema") or []
    api_cases = state.get("api_cases") or []
    target_url = state.get("target_url", "")
    base_url_override = state.get("base_url_override")
    base_url = (base_url_override or target_url).rstrip("/")
    auth_headers = state.get("auth_headers") or {}
    execution_policy = _normalize_api_execution_policy(state.get("api_execution_policy"))
    state["api_execution_policy"] = execution_policy
    write_allowed = _policy_allows_write(execution_policy)
    strategy = normalize_agent_strategy_decision(
        state.get("agent_strategy_decision") or {},
        parsed_api_schema=api_schema,
        execution_policy=execution_policy,
        test_type=str(state.get("test_type") or "auto"),
        source=str((state.get("agent_strategy_decision") or {}).get("source") or "state"),
    )
    if not state.get("agent_strategy_decision"):
        strategy = fallback_agent_strategy_decision(
            objective=state.get("objective"),
            parsed_api_schema=api_schema,
            execution_policy=execution_policy,
            test_type=str(state.get("test_type") or "auto"),
            reason="No planner strategy was present before API execution.",
        )
    state["agent_strategy_decision"] = strategy
    state["agent_tool_plan"] = strategy.get("tool_plan", [])
    state["agent_strategy_diagnostics"] = strategy.get("diagnostics", [])
    if state.get("agent_actions"):
        agent_actions = state.get("agent_actions") or []
    else:
        agent_actions = validate_and_record_agent_action_plan(
            state,
            stage="api_runner",
            strategy=strategy,
            parsed_api_schema=api_schema,
            execution_policy=execution_policy,
        )
    safe_schema_endpoints = safe_schema_method_endpoints(api_schema)
    selected_strategy_endpoints = strategy_selected_schema_endpoints(strategy, api_schema)
    all_safe_get_coverage_requested = strategy_requests_all_safe_coverage(strategy)
    focused_strategy_requested = strategy_requests_schema_endpoint_selection(strategy)
    if all_safe_get_coverage_requested:
        state["api_coverage_goal"] = ALL_SAFE_GET_COVERAGE_GOAL
    retry_count = max(0, int(getattr(settings, "API_REQUEST_RETRY_COUNT", 0) or 0))
    timeout_seconds = float(getattr(settings, "API_REQUEST_TIMEOUT_SECONDS", 30.0) or 30.0)
    execution_budget = _max_executed_requests()
    http_executed_count = 0
    environment_blocked_scopes: set[tuple[str, str]] = set()
    dependency_context: dict[str, object] = {}

    results = []

    if api_cases:
        if state.get("api_cases_generated"):
            api_cases, diagnostics = validate_generated_api_cases(
                api_cases,
                api_schema,
                execution_policy=execution_policy,
                allow_out_of_schema=bool(state.get("allow_out_of_schema_api_cases")),
                objective=state.get("objective"),
            )
        else:
            api_cases, diagnostics = sanitize_api_case_assertions(
                api_cases,
                api_schema,
                objective=state.get("objective"),
                downgrade_ungrounded_jsonpath=False,
            )
        if diagnostics:
            state.setdefault("agent_case_diagnostics", []).extend(diagnostics)
        state["api_cases"] = api_cases

    request_candidates = []
    selection_source = "fallback_url"
    fallback_reason = None
    strategy_coverage_endpoints: list[dict] = []
    if all_safe_get_coverage_requested:
        request_candidates = _build_test_requests(
            safe_schema_endpoints,
            base_url,
            auth_headers,
            execution_policy,
        )
        selection_source = ALL_SAFE_GET_COVERAGE_SOURCE
        fallback_reason = "strategy_selected_all_documented_safe_methods"
        strategy_coverage_endpoints = safe_schema_endpoints
    elif focused_strategy_requested:
        strategy_coverage_endpoints = selected_strategy_endpoints
        request_candidates = _build_test_requests(
            selected_strategy_endpoints,
            base_url,
            auth_headers,
            execution_policy,
        )
        selection_source = STRATEGY_SCHEMA_SOURCE
        fallback_reason = (
            "strategy_selected_documented_endpoint_scope"
            if selected_strategy_endpoints
            else "strategy_selected_no_valid_documented_endpoints"
        )
    elif api_cases:
        case_requests = _build_case_test_requests(
            api_cases,
            base_url,
            target_url,
            auth_headers,
            write_allowed=write_allowed,
        )
        if case_requests and (write_allowed or _has_executable_request(case_requests)):
            request_candidates = case_requests
            selection_source = "api_cases"
        else:
            fallback_reason = "curated_api_cases_not_executable_under_policy"
            if api_schema and not write_allowed:
                safe_schema = _safe_schema_subset(api_schema, execution_budget)
                fallback_requests = _build_test_requests(
                    safe_schema,
                    base_url,
                    auth_headers,
                    execution_policy,
                )
                if _has_executable_request(fallback_requests):
                    request_candidates = fallback_requests
                    selection_source = "safe_schema_fallback"
                else:
                    request_candidates = case_requests
                    selection_source = "api_cases"
            elif api_schema:
                request_candidates = _build_test_requests(
                    api_schema,
                    base_url,
                    auth_headers,
                    execution_policy,
                )
                selection_source = "schema_fallback"
            else:
                request_candidates = case_requests
                selection_source = "api_cases"
    elif api_schema:
        request_candidates = _build_test_requests(api_schema, base_url, auth_headers, execution_policy)
        selection_source = "parsed_api_schema"
    else:
        # Fallback: just hit the target URL
        fallback_url = _build_request_url("", base_url or target_url)
        if fallback_url:
            request_candidates.append({
                "label": f"GET {fallback_url}",
                "method": "GET",
                "url": fallback_url,
                "headers": auth_headers,
                "body": None,
                "expected_status": 200,
                "category": "SMOKE",
            })

    test_requests, request_selection = _select_requests_for_execution(
        request_candidates,
        execution_budget,
        source=selection_source,
        fallback_reason=fallback_reason,
    )
    if selection_source == ALL_SAFE_GET_COVERAGE_SOURCE:
        coverage_metadata = _all_safe_schema_coverage_metadata(safe_schema_endpoints, test_requests)
        coverage_metadata["bounded"] = bool(
            request_selection.get("bounded") or coverage_metadata.get("bounded")
        )
        coverage_metadata.update(
            {
                "coverage_scope": strategy.get("coverage_scope"),
                "strategy_intent": strategy.get("intent"),
                "strategy_source": strategy.get("source"),
                "strategy_summary": strategy_summary(strategy),
                "budget_behavior": (strategy.get("endpoint_selection") or {}).get("budget_behavior"),
                "strategy_coverage_completed": len(test_requests) > 0,
            }
        )
        request_selection.update(coverage_metadata)
    elif selection_source == STRATEGY_SCHEMA_SOURCE:
        coverage_metadata = _strategy_schema_coverage_metadata(
            strategy,
            strategy_coverage_endpoints,
            test_requests,
        )
        coverage_metadata["bounded"] = bool(
            request_selection.get("bounded") or coverage_metadata.get("bounded")
        )
        request_selection.update(coverage_metadata)
    state["api_request_selection"] = request_selection
    derive_action = find_agent_action(agent_actions, "api.derive_schema_requests")
    if derive_action and selection_source in {ALL_SAFE_GET_COVERAGE_SOURCE, STRATEGY_SCHEMA_SOURCE}:
        record_agent_action_observation(
            state,
            derive_action,
            stage="api_runner",
            status="success" if derive_action.get("allowed") else "blocked",
            output_summary={
                "source": selection_source,
                "candidate_total": request_selection.get("candidate_total"),
                "selected_total": request_selection.get("selected_total"),
                "omitted": request_selection.get("omitted"),
                "coverage_scope": request_selection.get("coverage_scope")
                or strategy.get("coverage_scope"),
                "strategy_coverage_completed": request_selection.get(
                    "strategy_coverage_completed"
                ),
            },
        )
    total_requests = len(test_requests)
    state["tool_summary"] = None
    artifacts = state.get("artifacts") or {}
    artifacts["execution_config"] = {
        "api_timeout_seconds": timeout_seconds,
        "api_retry_count": retry_count,
        "api_execution_policy": execution_policy,
        "api_max_executed_requests": execution_budget,
        "api_request_selection": request_selection,
    }
    state["artifacts"] = artifacts
    _update_api_execution_state(
        state,
        results,
        total_requests,
        complete=False,
        http_executed_count=http_executed_count,
        execution_budget=execution_budget,
    )
    await persist_progress(
        state,
        "api_runner",
        "running",
        _api_execution_progress_detail(total_requests, request_selection),
    )

    # Execute all test requests
    async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
        for index, req in enumerate(test_requests):
            if req.get("skip_reason"):
                record_tool_call(
                    state,
                    tool_name="api.safe_write_gate",
                    layer="api",
                    status="skipped",
                    input_summary={
                        "method": req.get("method"),
                        "url": req.get("url"),
                        "policy": execution_policy,
                    },
                    output_summary={"reason": req.get("skip_reason")},
                )
                results.append(_make_skipped_result(req, str(req.get("skip_reason"))))
                _update_api_execution_state(
                    state,
                    results,
                    total_requests,
                    complete=False,
                    http_executed_count=http_executed_count,
                    execution_budget=execution_budget,
                )
                await persist_progress(
                    state,
                    "api_runner",
                    "running",
                    (
                        f"Skipped {len(results)}/{total_requests}: "
                        f"{req.get('label', req.get('url', 'request'))}"
                    ),
                )
                continue
            missing_dependencies = _missing_dependencies(req, dependency_context)
            if missing_dependencies:
                reason = f"缺少上游提取变量：{', '.join(missing_dependencies)}"
                record_tool_call(
                    state,
                    tool_name="api.inject_dependency",
                    layer="api",
                    status="skipped",
                    input_summary={
                        "method": req.get("method"),
                        "url": req.get("url"),
                        "depends_on": missing_dependencies,
                    },
                    output_summary={"reason": reason},
                )
                results.append(_make_skipped_result(req, reason))
                _update_api_execution_state(
                    state,
                    results,
                    total_requests,
                    complete=False,
                    http_executed_count=http_executed_count,
                    execution_budget=execution_budget,
                )
                await persist_progress(
                    state,
                    "api_runner",
                    "running",
                    f"Skipped {len(results)}/{total_requests}: {req.get('label', req.get('url', 'request'))}",
                )
                continue

            if dependency_context:
                req = deepcopy(req)
                req["url"] = _substitute_context(req.get("url"), dependency_context)
                req["headers"] = _substitute_context(req.get("headers") or {}, dependency_context)
                req["body"] = _substitute_context(req.get("body"), dependency_context)
                req["query_params"] = _substitute_context(req.get("query_params") or {}, dependency_context)
                record_tool_call(
                    state,
                    tool_name="api.inject_dependency",
                    layer="api",
                    status="success",
                    input_summary={
                        "label": req.get("label"),
                        "variables_available": sorted(dependency_context.keys()),
                    },
                    output_summary={"resolved": True},
                )
            latest_auth_headers = state.get("auth_headers") or {}
            if (
                latest_auth_headers
                and str(req.get("category") or "").upper() != "AUTH"
                and has_auth_like_header(req.get("headers"))
            ):
                req = deepcopy(req)
                req["headers"] = {**(req.get("headers") or {}), **latest_auth_headers}
            environment_scope = _environment_block_scope(req)
            if environment_scope in environment_blocked_scopes:
                reason = _environment_block_reason(environment_scope[1], environment_scope[0])
                record_tool_call(
                    state,
                    tool_name="api.environment_method_gate",
                    layer="api",
                    status="skipped",
                    input_summary={
                        "method": req.get("method"),
                        "url": req.get("url"),
                        "scope": f"{environment_scope[0]} {environment_scope[1]}".strip(),
                    },
                    output_summary={"reason": reason},
                )
                results.append(_make_environment_skipped_result(req, reason))
                _update_api_execution_state(
                    state,
                    results,
                    total_requests,
                    complete=False,
                    http_executed_count=http_executed_count,
                    execution_budget=execution_budget,
                )
                await persist_progress(
                    state,
                    "api_runner",
                    "running",
                    f"Skipped {len(results)}/{total_requests}: {req.get('label', req.get('url', 'request'))}",
                )
                continue
            if execution_budget is not None and http_executed_count >= execution_budget:
                remaining = test_requests[index:]
                request_selection = dict(state.get("api_request_selection") or {})
                request_selection["runtime_budget_omitted"] = (
                    int(request_selection.get("runtime_budget_omitted") or 0) + len(remaining)
                )
                request_selection["omitted"] = (
                    int(request_selection.get("budget_omitted") or 0)
                    + int(request_selection.get("runtime_budget_omitted") or 0)
                )
                request_selection["bounded"] = request_selection["omitted"] > 0
                state["api_request_selection"] = request_selection
                record_tool_call(
                    state,
                    tool_name="api.execution_budget",
                    layer="api",
                    status="skipped",
                    input_summary={
                        "max_executed_requests": execution_budget,
                        "http_executed": http_executed_count,
                    },
                    output_summary={"omitted_remaining": len(remaining)},
                )
                _update_api_execution_state(
                    state,
                    results,
                    total_requests,
                    complete=False,
                    http_executed_count=http_executed_count,
                    execution_budget=execution_budget,
                )
                await persist_progress(
                    state,
                    "api_runner",
                    "running",
                    f"Omitted remaining {len(remaining)} API request(s) after execution budget was reached",
                )
                break
            _record_mock_body_generation(state, req)
            try:
                start = time.perf_counter()
                remaining_budget = None if execution_budget is None else execution_budget - http_executed_count
                resp, attempts, request_error = await _request_with_retry(
                    client,
                    req,
                    retry_count,
                    remaining_budget,
                )
                elapsed = round((time.perf_counter() - start) * 1000, 2)
                http_executed_count += attempts
                if request_error is not None or resp is None:
                    raise request_error or RuntimeError("HTTP request failed")
                _record_http_request_call(state, req, resp.status_code, attempts, elapsed)

                payload = _response_payload(resp)
                stored_body = _stored_response_body(payload)

                if str(req.get("method") or "").upper() in WRITE_API_METHODS and resp.status_code == 405:
                    environment_blocked_scopes.add(environment_scope)
                    reason = _environment_block_reason(environment_scope[1], environment_scope[0])
                    record_tool_call(
                        state,
                        tool_name="api.environment_method_gate",
                        layer="api",
                        status="skipped",
                        input_summary={
                            "method": req.get("method"),
                            "url": req.get("url"),
                            "status_code": resp.status_code,
                        },
                        output_summary={"reason": reason},
                        elapsed_ms=elapsed,
                    )
                    results.append(
                        _make_environment_skipped_result(
                            req,
                            reason,
                            status_code=resp.status_code,
                            elapsed_ms=elapsed,
                            body=stored_body,
                        )
                    )
                    _update_api_execution_state(
                        state,
                        results,
                        total_requests,
                        complete=False,
                        http_executed_count=http_executed_count,
                        execution_budget=execution_budget,
                    )
                    await persist_progress(
                        state,
                        "api_runner",
                        "running",
                        f"Skipped {len(results)}/{total_requests}: {req.get('label', req.get('url', 'request'))}",
                    )
                    continue

                auth_refreshed = False
                if _is_auth_failure(resp.status_code, payload) and _can_refresh_auth(state, req):
                    if execution_budget is not None and http_executed_count >= execution_budget:
                        refreshed_headers = None
                    else:
                        refreshed_headers = await _refresh_auth_for_request(state, req, api_schema or None)
                    if refreshed_headers:
                        req = deepcopy(req)
                        req["headers"] = {**(req.get("headers") or {}), **refreshed_headers}
                        auth_refreshed = True
                        start = time.perf_counter()
                        remaining_budget = None if execution_budget is None else execution_budget - http_executed_count
                        resp, attempts, request_error = await _request_with_retry(
                            client,
                            req,
                            retry_count,
                            remaining_budget,
                        )
                        elapsed = round((time.perf_counter() - start) * 1000, 2)
                        http_executed_count += attempts
                        if request_error is not None or resp is None:
                            raise request_error or RuntimeError("HTTP request failed after auth refresh")
                        _record_http_request_call(
                            state,
                            req,
                            resp.status_code,
                            attempts,
                            elapsed,
                            auth_refresh_retry=True,
                        )
                        payload = _response_payload(resp)
                        stored_body = _stored_response_body(payload)

                expected_status = req.get("expected_status", 200)
                category = req.get("category", "SMOKE")

                # Determine pass/fail
                if category == "SMOKE":
                    passed = _status_matches(200, resp.status_code, payload)
                else:
                    passed = _status_matches(expected_status, resp.status_code, payload)

                assertion_results = _evaluate_assertions(req, resp.status_code, payload)
                auth_advisory_reason = _auth_negative_success_advisory(req, resp.status_code, payload)
                business_rejection_warning = _param_validation_business_rejection(req, resp.status_code, payload)
                if business_rejection_warning:
                    _mark_business_rejection_assertions(assertion_results, business_rejection_warning)
                if auth_advisory_reason:
                    _mark_assertions_advisory(assertion_results, auth_advisory_reason)
                for assertion_result in assertion_results:
                    assertion_tool = {
                        "status_code": "api.status_assert",
                        "json_path": "api.json_path_assert",
                        "json_type": "api.schema_assert",
                        "schema": "api.schema_assert",
                    }.get(assertion_result.get("type"), "api.json_path_assert")
                    assertion_status = "success" if assertion_result.get("passed") in {True, None} else "failed"
                    if assertion_result.get("skipped"):
                        assertion_status = "skipped"
                    record_tool_call(
                        state,
                        tool_name=assertion_tool,
                        layer="api",
                        status=assertion_status,
                        input_summary={
                            "label": req.get("label"),
                            "type": assertion_result.get("type"),
                            "path": assertion_result.get("path"),
                            "blocking": assertion_result.get("blocking", True),
                        },
                        output_summary={
                            "passed": assertion_result.get("passed"),
                            "skipped": assertion_result.get("skipped", False),
                            "error": assertion_result.get("error"),
                        },
                    )

                if auth_advisory_reason:
                    passed = None
                    failure_classification = None
                elif business_rejection_warning:
                    passed = True
                    failure_classification = None
                else:
                    passed = _assertions_pass(assertion_results, passed)
                    failure_classification = None if passed else _classify_api_failure(
                        req,
                        resp.status_code,
                        payload,
                        assertion_results,
                    )

                extractions = []
                for spec in _extract_specs(req.get("extract")):
                    actual = _json_path_get(payload, spec["path"]) if isinstance(payload, (dict, list)) else None
                    extracted = actual is not None
                    if extracted:
                        dependency_context[spec["name"]] = actual
                    extraction_entry = {
                        "name": spec["name"],
                        "path": spec["path"],
                        "extracted": extracted,
                    }
                    extractions.append(extraction_entry)
                    record_tool_call(
                        state,
                        tool_name="api.extract_value",
                        layer="api",
                        status="success" if extracted else "failed",
                        input_summary={"label": req.get("label"), "name": spec["name"], "path": spec["path"]},
                        output_summary={"extracted": extracted},
                    )

                result_entry = {
                    "label": req["label"],
                    "method": req["method"],
                    "url": req["url"],
                    "status_code": resp.status_code,
                    "envelope_status_code": _payload_status_code(payload),
                    "elapsed_ms": elapsed,
                    "body": stored_body,
                    "request_headers": redact_sensitive_headers(req.get("headers", {})),
                    "request_body": redact_sensitive_data(req.get("body")),
                    "request_body_source": req.get("request_body_source"),
                    "passed": passed,
                    "category": req.get("category", "SMOKE"),
                    "assertion_results": assertion_results,
                    "extractions": extractions,
                    "auth_refreshed": auth_refreshed,
                    "http_executed": True,
                }
                if auth_advisory_reason:
                    result_entry.update(
                        {
                            "skipped": True,
                            "skip_type": "auth_advisory",
                            "skip_reason": auth_advisory_reason,
                            "advisory": True,
                            "advisory_type": "auth_negative_unexpected_success",
                            "warning": auth_advisory_reason,
                        }
                    )
                elif business_rejection_warning:
                    result_entry.update(
                        {
                            "accepted_error_envelope": True,
                            "warning": business_rejection_warning,
                            "warning_type": "validation_business_error_envelope",
                        }
                    )
                if failure_classification:
                    result_entry.update(failure_classification)
                results.append(result_entry)
            except Exception as e:
                record_tool_call(
                    state,
                    tool_name="api.http_request",
                    layer="api",
                    status="failed",
                    input_summary={"method": req.get("method"), "url": req.get("url")},
                    output_summary={"error": str(e)[:300]},
                )
                results.append({
                    "label": req.get("label", f"{req['method']} {req['url']}"),
                    "method": req["method"],
                    "url": req["url"],
                    "status_code": 0,
                    "elapsed_ms": 0,
                    "body": None,
                    "request_headers": redact_sensitive_headers(req.get("headers", {})),
                    "request_body": redact_sensitive_data(req.get("body")),
                    "request_body_source": req.get("request_body_source"),
                    "passed": False,
                    "category": req.get("category", "SMOKE"),
                    "error": sanitize_persisted_text(str(e)),
                    "http_executed": True,
                })
            _update_api_execution_state(
                state,
                results,
                total_requests,
                complete=False,
                http_executed_count=http_executed_count,
                execution_budget=execution_budget,
            )
            await persist_progress(
                state,
                "api_runner",
                "running",
                (
                    f"Executed {len(results)}/{total_requests}: "
                    f"{req.get('label', req.get('url', 'request'))}"
                ),
            )

    total = len(results)
    skipped_count = sum(1 for r in results if r.get("skipped"))
    executed_count = total - skipped_count
    passed_count = sum(1 for r in results if r.get("passed") is True)
    failed_count = max(executed_count - passed_count, 0)
    all_passed = total > 0 and failed_count == 0

    _update_api_execution_state(
        state,
        results,
        total_requests,
        complete=True,
        http_executed_count=http_executed_count,
        execution_budget=execution_budget,
    )
    state["tool_summary"] = summarize_tool_calls(state.get("tool_calls"))
    artifacts = state.get("artifacts") or {}
    execution_config = artifacts.get("execution_config")
    if isinstance(execution_config, dict):
        execution_config["api_request_selection"] = state.get("api_request_selection") or {}
    artifacts["tool_calls"] = state.get("tool_calls", [])
    artifacts["tool_summary"] = state["tool_summary"]
    state["artifacts"] = artifacts

    status = "done" if all_passed else "failed"
    detail = (
        f"Executed {executed_count} API request(s): "
        f"{passed_count} passed, {failed_count} failed, {skipped_count} skipped"
    )
    request_selection = state.get("api_request_selection") or {}
    if request_selection.get("source") == ALL_SAFE_GET_COVERAGE_SOURCE:
        detail += (
            " (schema-driven all-safe-GET coverage "
            f"{request_selection.get('selected_safe_endpoint_total', total_requests)}/"
            f"{request_selection.get('safe_endpoint_total', total_requests)} safe endpoint(s)"
        )
        if request_selection.get("omitted_safe_endpoint_total"):
            detail += f", {request_selection.get('omitted_safe_endpoint_total')} omitted by budget"
        detail += ")"
    state.setdefault("workflow_steps", []).append(
        {"node": "api_runner", "status": status, "detail": detail}
    )
    await persist_progress(state, "api_runner", status, detail)
    return state

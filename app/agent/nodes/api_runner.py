import json
import logging
import time

import httpx

from app.agent.state import AgentState

logger = logging.getLogger(__name__)


def _parse_status(val) -> int:
    """Parse response status, handling 'default' and non-numeric values."""
    try:
        return int(val)
    except (ValueError, TypeError):
        return 200


def _build_test_requests(api_schema: list[dict], base_url: str, headers: dict | None = None) -> list[dict]:
    """Generate test requests from parsed API schema: smoke, param validation, missing required, boundary, unauthorized."""
    requests = []
    default_headers = headers or {}

    for endpoint in api_schema:
        path = endpoint.get("path", "")
        method = endpoint.get("method", "GET").upper()
        full_url = f"{base_url.rstrip('/')}{path}"
        req_body = endpoint.get("example_request")
        required_fields = endpoint.get("required_fields", [])
        auth_required = endpoint.get("auth_required", False)

        # 1. Smoke test — use example request as-is
        requests.append({
            "label": f"SMOKE {method} {path}",
            "method": method,
            "url": full_url,
            "headers": {**default_headers, **({"Content-Type": endpoint.get("request_body_content_type", "application/json")} if req_body else {})},
            "body": req_body,
            "expected_status": _parse_status(endpoint.get("response_status", "200")),
            "category": "SMOKE",
        })

        # 2. Missing required fields (POST/PUT/PATCH only)
        if method in ("POST", "PUT", "PATCH") and required_fields and req_body and isinstance(req_body, dict):
            for field in required_fields[:3]:  # limit to 3 to avoid explosion
                broken_body = {k: v for k, v in req_body.items() if k != field}
                requests.append({
                    "label": f"MISSING_FIELD {method} {path} (no {field})",
                    "method": method,
                    "url": full_url,
                    "headers": {**default_headers, "Content-Type": endpoint.get("request_body_content_type", "application/json")},
                    "body": broken_body,
                    "expected_status": 400,
                    "category": "PARAM_VALIDATION",
                })

        # 3. Empty body for POST/PUT/PATCH
        if method in ("POST", "PUT", "PATCH"):
            requests.append({
                "label": f"EMPTY_BODY {method} {path}",
                "method": method,
                "url": full_url,
                "headers": {**default_headers, "Content-Type": "application/json"},
                "body": {},
                "expected_status": 400,
                "category": "PARAM_VALIDATION",
            })

        # 4. Unauthorized test
        if auth_required:
            requests.append({
                "label": f"UNAUTHORIZED {method} {path}",
                "method": method,
                "url": full_url,
                "headers": {"Content-Type": "application/json"} if req_body else {},
                "body": req_body,
                "expected_status": 401,
                "category": "AUTH",
            })

        # 5. Invalid type for string fields
        if method in ("POST", "PUT", "PATCH") and req_body and isinstance(req_body, dict):
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
                    "headers": {**default_headers, "Content-Type": endpoint.get("request_body_content_type", "application/json")},
                    "body": bad_body,
                    "expected_status": 400,
                    "category": "PARAM_VALIDATION",
                })

    return requests


async def run(state: AgentState) -> AgentState:
    api_schema = state.get("parsed_api_schema") or []
    api_cases = state.get("api_cases") or []
    api_plan = state.get("api_plan")
    target_url = state.get("target_url", "")
    base_url = target_url.rstrip("/")

    results = []

    # If we have API schema, generate requests from it
    if api_schema:
        test_requests = _build_test_requests(api_schema, base_url)
    elif api_cases:
        # Generate from api_cases (from case_generator)
        test_requests = []
        for case in api_cases:
            tmpl = case.get("request_template", {})
            if tmpl:
                method = tmpl.get("method", "GET").upper()
                url = tmpl.get("url", "")
                if url and not url.startswith("http"):
                    url = f"{base_url}{url}"
                test_requests.append({
                    "label": case.get("title", f"{method} {url}"),
                    "method": method,
                    "url": url or target_url,
                    "headers": tmpl.get("headers", {}),
                    "body": tmpl.get("body"),
                    "query_params": tmpl.get("query_params", {}),
                    "expected_status": 200,
                    "category": case.get("category", "SMOKE"),
                    "assertions": case.get("assertions", []),
                })
    else:
        # Fallback: just hit the target URL
        test_requests = [{
            "label": f"GET {target_url}",
            "method": "GET",
            "url": target_url,
            "headers": {},
            "body": None,
            "expected_status": 200,
            "category": "SMOKE",
        }]

    # Execute all test requests
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for req in test_requests:
            try:
                start = time.perf_counter()
                resp = await client.request(
                    req["method"],
                    req["url"],
                    headers=req.get("headers") or None,
                    json=req.get("body"),
                    params=req.get("query_params"),
                )
                elapsed = round((time.perf_counter() - start) * 1000, 2)

                try:
                    payload = resp.json()
                except Exception:
                    payload = resp.text[:500]

                expected_status = req.get("expected_status", 200)
                passed = resp.status_code == expected_status

                # Run assertions if present
                assertion_results = []
                for assertion in req.get("assertions", []):
                    atype = assertion.get("type", "")
                    if atype == "status_code":
                        a_passed = resp.status_code == assertion.get("expected", 200)
                        assertion_results.append({"type": atype, "passed": a_passed})
                    elif atype == "json_path" and isinstance(payload, dict):
                        path_expr = assertion.get("path", "")
                        # Simple $.key resolution
                        parts = path_expr.lstrip("$.").split(".")
                        val = payload
                        for p in parts:
                            if isinstance(val, dict):
                                val = val.get(p)
                            else:
                                val = None
                                break
                        if assertion.get("expected") == "not_null":
                            a_passed = val is not None
                        else:
                            a_passed = val == assertion.get("expected")
                        assertion_results.append({"type": atype, "passed": a_passed, "actual": val})

                if assertion_results:
                    passed = all(a.get("passed") for a in assertion_results)

                results.append({
                    "label": req["label"],
                    "method": req["method"],
                    "url": req["url"],
                    "status_code": resp.status_code,
                    "elapsed_ms": elapsed,
                    "body": payload if isinstance(payload, (dict, list)) else str(payload)[:500],
                    "passed": passed,
                    "category": req.get("category", "SMOKE"),
                    "assertion_results": assertion_results,
                })
            except Exception as e:
                results.append({
                    "label": req.get("label", f"{req['method']} {req['url']}"),
                    "method": req["method"],
                    "url": req["url"],
                    "status_code": 0,
                    "elapsed_ms": 0,
                    "body": None,
                    "passed": False,
                    "category": req.get("category", "SMOKE"),
                    "error": str(e),
                })

    total = len(results)
    passed_count = sum(1 for r in results if r.get("passed"))
    all_passed = passed_count == total

    state["api_execution_result"] = {
        "total": total,
        "passed": passed_count,
        "failed": total - passed_count,
        "pass_rate": f"{round(passed_count / total * 100, 1)}%" if total else "0%",
        "results": results,
        "all_passed": all_passed,
    }

    # Also set legacy execution_result for backward compat
    state["execution_result"] = {
        "status_code": 0 if all_passed else 1,
        "stdout": json.dumps(results, ensure_ascii=False, default=str)[:3000],
        "stderr": "" if all_passed else f"{total - passed_count} API test(s) failed",
        "trace_path": None,
        "api_results": results,
    }

    status = "done" if all_passed else "failed"
    state.setdefault("workflow_steps", []).append(
        {
            "node": "api_runner",
            "status": status,
            "detail": f"Executed {total} API test(s): {passed_count} passed, {total - passed_count} failed",
        }
    )
    return state

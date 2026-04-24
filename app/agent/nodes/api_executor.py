import json
import logging
import time

import httpx

from app.agent.state import AgentState

logger = logging.getLogger(__name__)


async def run(state: AgentState) -> AgentState:
    test_plan = state.get("test_plan") or []
    target_url = state.get("target_url", "")
    results = []

    for plan_item in test_plan:
        steps = plan_item.get("steps", [])
        for step in steps:
            # Each step can be a dict with method/url/body or a string description
            if isinstance(step, dict) and "method" in step:
                method = step["method"].upper()
                url = step.get("url", target_url)
                headers = step.get("headers")
                body = step.get("body")
                try:
                    start = time.perf_counter()
                    resp = httpx.request(method, url, headers=headers, json=body, timeout=30.0)
                    elapsed = round((time.perf_counter() - start) * 1000, 2)
                    try:
                        payload = resp.json()
                    except Exception:
                        payload = resp.text
                    results.append({
                        "method": method,
                        "url": url,
                        "status_code": resp.status_code,
                        "elapsed_ms": elapsed,
                        "body": payload,
                        "passed": 200 <= resp.status_code < 400,
                    })
                except Exception as e:
                    results.append({
                        "method": method,
                        "url": url,
                        "status_code": 0,
                        "elapsed_ms": 0,
                        "body": None,
                        "passed": False,
                        "error": str(e),
                    })

    # If no executable steps found, do a basic GET on target_url
    if not results and target_url:
        try:
            start = time.perf_counter()
            resp = httpx.get(target_url, timeout=30.0)
            elapsed = round((time.perf_counter() - start) * 1000, 2)
            try:
                payload = resp.json()
            except Exception:
                payload = resp.text
            results.append({
                "method": "GET",
                "url": target_url,
                "status_code": resp.status_code,
                "elapsed_ms": elapsed,
                "body": payload,
                "passed": 200 <= resp.status_code < 400,
            })
        except Exception as e:
            results.append({
                "method": "GET",
                "url": target_url,
                "status_code": 0,
                "elapsed_ms": 0,
                "body": None,
                "passed": False,
                "error": str(e),
            })

    all_passed = all(r.get("passed") for r in results) if results else False
    state["execution_result"] = {
        "status_code": 0 if all_passed else 1,
        "stdout": json.dumps(results, ensure_ascii=False, default=str),
        "stderr": "" if all_passed else "Some API tests failed",
        "trace_path": None,
        "api_results": results,
    }
    status = "done" if all_passed else "failed"
    state.setdefault("workflow_steps", []).append(
        {"node": "api_executor", "status": status, "detail": f"Executed {len(results)} API test(s), {sum(1 for r in results if r.get('passed'))} passed"}
    )
    return state

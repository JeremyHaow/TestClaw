import json
import time
import logging

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.dependencies import CurrentUser, DbSession

logger = logging.getLogger(__name__)
router = APIRouter()


class ApiTestRequest(BaseModel):
    method: str = "GET"
    url: str
    headers: dict | None = None
    body: dict | None = None
    expected_status: int | None = 200


@router.post("/execute")
async def execute_api_test(payload: ApiTestRequest, _: CurrentUser):
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(
                payload.method.upper(),
                payload.url,
                headers=payload.headers,
                json=payload.body,
            )
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        try:
            body = resp.json()
        except Exception:
            body = resp.text

        passed = True
        assertion_error = None
        if payload.expected_status and resp.status_code != payload.expected_status:
            passed = False
            assertion_error = f"Expected status {payload.expected_status}, got {resp.status_code}"

        return {
            "status_code": resp.status_code,
            "elapsed_ms": elapsed,
            "body": body,
            "headers": dict(resp.headers),
            "passed": passed,
            "assertion_error": assertion_error,
        }
    except Exception as e:
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        return {
            "status_code": 0,
            "elapsed_ms": elapsed,
            "body": None,
            "headers": {},
            "passed": False,
            "assertion_error": str(e),
        }


class BatchApiTestRequest(BaseModel):
    environment_url: str
    endpoints: list[ApiTestRequest]


@router.post("/execute-batch")
async def execute_batch_api_tests(payload: BatchApiTestRequest, _: CurrentUser):
    results = []
    for ep in payload.endpoints:
        url = ep.url if ep.url.startswith("http") else f"{payload.environment_url.rstrip('/')}{ep.url}"
        req = ApiTestRequest(method=ep.method, url=url, headers=ep.headers, body=ep.body, expected_status=ep.expected_status)
        result = await execute_api_test(req, _)
        results.append({"endpoint": url, "method": ep.method, **result})
    passed = sum(1 for r in results if r.get("passed"))
    return {"results": results, "total": len(results), "passed": passed, "failed": len(results) - passed}

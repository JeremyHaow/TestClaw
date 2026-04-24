import json
import time

import httpx
from langchain_core.tools import tool
from openapi_schema_validator import validate
from openapi_schema_validator.validation.exceptions import OpenAPIValidationError


@tool
def execute_api_test(
    method: str,
    url: str,
    headers: dict | None = None,
    json_body: dict | None = None,
    schema: dict | None = None,
) -> dict:
    """Execute an HTTP request and optionally validate the JSON response against a schema."""
    start = time.perf_counter()
    response = httpx.request(method.upper(), url, headers=headers, json=json_body, timeout=30.0)
    duration = time.perf_counter() - start
    schema_valid = None
    schema_error = None
    payload = None
    try:
        payload = response.json()
    except json.JSONDecodeError:
        payload = response.text
    if schema and isinstance(payload, dict):
        try:
            validate(payload, schema)
            schema_valid = True
        except OpenAPIValidationError as exc:
            schema_valid = False
            schema_error = str(exc)
    return {
        "status_code": response.status_code,
        "elapsed_ms": round(duration * 1000, 2),
        "body": payload,
        "schema_valid": schema_valid,
        "schema_error": schema_error,
    }

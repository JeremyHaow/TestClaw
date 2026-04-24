import json
from typing import Any

import yaml
from langchain_core.tools import tool


def _extract_parameters(operation: dict, path_item: dict) -> dict:
    """Extract path, query, header params from operation + path-level parameters."""
    all_params = list(path_item.get("parameters", [])) + list(operation.get("parameters", []))
    path_params, query_params, header_params = [], [], []
    seen = set()
    for p in all_params:
        if not isinstance(p, dict):
            continue
        name = p.get("name", "")
        loc = p.get("in", "")
        key = f"{loc}:{name}"
        if key in seen:
            continue
        seen.add(key)
        entry = {
            "name": name,
            "in": loc,
            "required": p.get("required", False),
            "type": p.get("type") or (p.get("schema", {}) or {}).get("type", "string"),
            "description": p.get("description", ""),
        }
        if loc == "path":
            path_params.append(entry)
        elif loc == "query":
            query_params.append(entry)
        elif loc == "header":
            header_params.append(entry)
    return {"path_params": path_params, "query_params": query_params, "header_params": header_params}


def _extract_request_body_v3(operation: dict) -> dict | None:
    """Extract request body schema from OpenAPI 3.x requestBody."""
    rb = operation.get("requestBody")
    if not rb or not isinstance(rb, dict):
        return None
    content = rb.get("content", {})
    for ct in ("application/json", "application/x-www-form-urlencoded", "multipart/form-data"):
        if ct in content:
            schema = content[ct].get("schema", {})
            return {"content_type": ct, "schema": schema, "required": rb.get("required", False)}
    # Fallback: take first content type
    if content:
        ct, body = next(iter(content.items()))
        return {"content_type": ct, "schema": body.get("schema", {}), "required": rb.get("required", False)}
    return None


def _extract_request_body_v2(operation: dict) -> dict | None:
    """Extract request body schema from Swagger 2.0 body parameter."""
    for p in operation.get("parameters", []):
        if isinstance(p, dict) and p.get("in") == "body":
            return {
                "content_type": "application/json",
                "schema": p.get("schema", {}),
                "required": p.get("required", False),
            }
    return None


def _extract_responses(operation: dict) -> dict | None:
    """Extract primary response schema."""
    responses = operation.get("responses", {})
    for code in ("200", "201", "200", "default"):
        if code in responses:
            resp = responses[code]
            if not isinstance(resp, dict):
                continue
            # OpenAPI 3.x
            content = resp.get("content", {})
            if content:
                for ct in ("application/json",):
                    if ct in content:
                        return {"status_code": code, "schema": content[ct].get("schema", {})}
            # Swagger 2.0
            schema = resp.get("schema")
            if schema:
                return {"status_code": code, "schema": schema}
            return {"status_code": code, "schema": {}}
    return None


def _has_auth(operation: dict, document: dict) -> bool:
    """Check if endpoint requires authentication."""
    op_security = operation.get("security")
    if op_security is not None:
        return len(op_security) > 0
    global_security = document.get("security", [])
    return len(global_security) > 0


def _generate_example(schema: dict, document: dict | None = None, depth: int = 0) -> Any:
    """Generate a simple example value from a JSON schema."""
    if depth > 5:
        return None
    if not isinstance(schema, dict):
        return None

    # Handle $ref
    ref = schema.get("$ref")
    if ref and document:
        parts = ref.lstrip("#/").split("/")
        resolved = document
        for p in parts:
            if isinstance(resolved, dict):
                resolved = resolved.get(p, {})
        if isinstance(resolved, dict):
            return _generate_example(resolved, document, depth + 1)

    schema_type = schema.get("type", "object")

    if schema_type == "string":
        fmt = schema.get("format", "")
        if fmt == "date-time":
            return "2024-01-01T00:00:00Z"
        if fmt == "date":
            return "2024-01-01"
        if fmt == "email":
            return "user@example.com"
        enum = schema.get("enum")
        if enum:
            return enum[0]
        return "string"
    if schema_type == "integer":
        return schema.get("minimum", 1)
    if schema_type == "number":
        return schema.get("minimum", 1.0)
    if schema_type == "boolean":
        return True
    if schema_type == "array":
        items = schema.get("items", {})
        example = _generate_example(items, document, depth + 1)
        return [example] if example is not None else []
    if schema_type == "object" or "properties" in schema:
        props = schema.get("properties", {})
        result = {}
        for name, prop in props.items():
            if isinstance(prop, dict):
                result[name] = _generate_example(prop, document, depth + 1)
        return result
    return None


def _extract_required_fields(schema: dict) -> list[str]:
    """Extract required field names from schema."""
    if not isinstance(schema, dict):
        return []
    return schema.get("required", [])


def _normalize_openapi_document_v3(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse OpenAPI 3.x document into rich endpoint descriptors."""
    endpoints: list[dict[str, Any]] = []
    for path, path_item in document.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            if not isinstance(operation, dict):
                continue

            params = _extract_parameters(operation, path_item)
            request_body = _extract_request_body_v3(operation)
            response = _extract_responses(operation)

            endpoint = {
                "path": path,
                "method": method.upper(),
                "summary": operation.get("summary", ""),
                "operationId": operation.get("operationId", ""),
                "tags": operation.get("tags", []),
                "path_params": params["path_params"],
                "query_params": params["query_params"],
                "header_params": params["header_params"],
                "request_body_schema": request_body["schema"] if request_body else None,
                "request_body_content_type": request_body["content_type"] if request_body else None,
                "response_schema": response["schema"] if response else None,
                "response_status": response["status_code"] if response else "200",
                "required_fields": (
                    _extract_required_fields(request_body["schema"]) if request_body else []
                ) + [p["name"] for p in params["path_params"] if p.get("required")],
                "auth_required": _has_auth(operation, document),
                "example_request": (
                    _generate_example(request_body["schema"], document) if request_body else None
                ),
                "example_response": (
                    _generate_example(response["schema"], document) if response else None
                ),
            }
            endpoints.append(endpoint)
    return endpoints


def _normalize_openapi_document_v2(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse Swagger 2.0 document into rich endpoint descriptors."""
    endpoints: list[dict[str, Any]] = []
    definitions = document.get("definitions", {})

    def resolve_ref(ref: str) -> dict:
        if not ref.startswith("#/"):
            return {}
        parts = ref.lstrip("#/").split("/")
        node = document
        for p in parts:
            if isinstance(node, dict):
                node = node.get(p, {})
        return node if isinstance(node, dict) else {}

    for path, path_item in document.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            if not isinstance(operation, dict):
                continue

            params = _extract_parameters(operation, path_item)
            request_body = _extract_request_body_v2(operation)
            response = _extract_responses(operation)

            # Resolve schema refs for request body
            req_schema = request_body["schema"] if request_body else None
            if req_schema and "$ref" in req_schema:
                req_schema = resolve_ref(req_schema["$ref"])

            # Resolve schema refs for response
            resp_schema = response["schema"] if response else None
            if resp_schema and "$ref" in resp_schema:
                resp_schema = resolve_ref(resp_schema["$ref"])

            endpoint = {
                "path": path,
                "method": method.upper(),
                "summary": operation.get("summary", ""),
                "operationId": operation.get("operationId", ""),
                "tags": operation.get("tags", []),
                "path_params": params["path_params"],
                "query_params": params["query_params"],
                "header_params": params["header_params"],
                "request_body_schema": req_schema,
                "request_body_content_type": request_body["content_type"] if request_body else None,
                "response_schema": resp_schema,
                "response_status": response["status_code"] if response else "200",
                "required_fields": (
                    _extract_required_fields(req_schema) if req_schema else []
                ) + [p["name"] for p in params["path_params"] if p.get("required")],
                "auth_required": _has_auth(operation, document),
                "example_request": _generate_example(req_schema, document) if req_schema else None,
                "example_response": _generate_example(resp_schema, document) if resp_schema else None,
            }
            endpoints.append(endpoint)
    return endpoints


def _normalize_openapi_document(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Route to the correct parser based on document version."""
    version = document.get("openapi", document.get("swagger", ""))
    if version.startswith("3"):
        return _normalize_openapi_document_v3(document)
    if version.startswith("2"):
        return _normalize_openapi_document_v2(document)
    # Try v3 first, fallback to v2
    endpoints = _normalize_openapi_document_v3(document)
    if endpoints:
        return endpoints
    return _normalize_openapi_document_v2(document)


def _normalize_postman_collection(document: dict[str, Any]) -> list[dict[str, Any]]:
    endpoints: list[dict[str, Any]] = []

    def walk(items: list[dict[str, Any]]) -> None:
        for item in items:
            if "request" in item:
                request = item["request"]
                method = request.get("method", "GET")
                url = request.get("url", {})
                raw_url = url if isinstance(url, str) else url.get("raw")
                endpoints.append(
                    {
                        "path": raw_url,
                        "method": method.upper(),
                        "summary": item.get("name"),
                        "operationId": item.get("name"),
                        "tags": [],
                        "path_params": [],
                        "query_params": [],
                        "header_params": [],
                        "request_body_schema": None,
                        "request_body_content_type": None,
                        "response_schema": None,
                        "response_status": "200",
                        "required_fields": [],
                        "auth_required": False,
                        "example_request": None,
                        "example_response": None,
                    }
                )
            if "item" in item:
                walk(item["item"])

    walk(document.get("item", []))
    return endpoints


def parse_api_document_content(raw_content: str, format_hint: str | None = None) -> list[dict[str, Any]]:
    text = raw_content.strip()
    if not text:
        return []

    data: dict[str, Any]
    if format_hint in {"yaml", "yml"}:
        data = yaml.safe_load(text)
    else:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = yaml.safe_load(text)

    if not isinstance(data, dict):
        raise ValueError("Unsupported API document format")
    if "openapi" in data or "swagger" in data:
        return _normalize_openapi_document(data)
    if "info" in data and "item" in data:
        return _normalize_postman_collection(data)
    raise ValueError("Unsupported API document content")


@tool
def parse_api_document(raw_content: str, format_hint: str | None = None) -> list[dict[str, Any]]:
    """Parse OpenAPI or Postman collection content into structured endpoints."""
    return parse_api_document_content(raw_content, format_hint)

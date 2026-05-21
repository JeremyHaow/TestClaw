import logging
import re
from urllib.parse import urljoin, urlparse

import httpx
import yaml

from app.agent.progress import persist_progress
from app.agent.state import AgentState
from app.agent.tool_registry import install_tool_context, record_tool_call
from app.tools.doc_parser import parse_api_document_content

logger = logging.getLogger(__name__)

# Patterns to detect Swagger/OpenAPI URLs
_SWAGGER_URL_PATTERNS = [
    re.compile(r"/swagger\.json$", re.I),
    re.compile(r"/swagger\.yaml$", re.I),
    re.compile(r"/openapi\.json$", re.I),
    re.compile(r"/openapi\.yaml$", re.I),
    re.compile(r"/api-docs", re.I),
    re.compile(r"/v[12]/swagger", re.I),
    re.compile(r"/docs/api", re.I),
]


def _looks_like_swagger_url(url: str) -> bool:
    return any(p.search(url) for p in _SWAGGER_URL_PATTERNS)


def _extract_base_url(swagger_url: str) -> str:
    """Extract API base URL from Swagger URL by stripping the document path."""
    import re
    # Remove common Swagger/OpenAPI doc paths
    patterns = [
        r"/swagger\.json$",
        r"/swagger\.yaml$",
        r"/openapi\.json$",
        r"/openapi\.yaml$",
        r"/spec\.json$",
        r"/spec\.yaml$",
        r"/api-docs.*$",
        r"/api\.json$",
        r"/api\.yaml$",
        r"/v[123]/swagger.*$",
        r"/v[123]/openapi.*$",
        r"/docs/api.*$",
        r"/swagger/v[123]/.*$",
    ]
    url = swagger_url.rstrip("/")
    for p in patterns:
        url = re.sub(p, "", url, flags=re.I)
    # Generic fallback: if URL still ends with a .json/.yaml file, strip it
    url = re.sub(r"/[^/]+\.(json|yaml|yml)$", "", url, flags=re.I)
    return url


def _absolute_http_url(value: str, source_url: str | None = None) -> str | None:
    value = value.strip()
    if value.startswith(("http://", "https://")):
        return value.rstrip("/")
    if not source_url:
        return None
    parsed = urlparse(source_url)
    if not parsed.scheme or not parsed.netloc:
        return None
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if value.startswith("/"):
        return f"{origin}{value}".rstrip("/")
    return urljoin(f"{_extract_base_url(source_url).rstrip('/')}/", value).rstrip("/")


def _extract_document_base_url(document_content: str, source_url: str | None = None) -> str | None:
    try:
        import json

        try:
            document = json.loads(document_content)
        except json.JSONDecodeError:
            document = yaml.safe_load(document_content)
    except Exception:
        return None

    if not isinstance(document, dict):
        return None

    servers = document.get("servers")
    if isinstance(servers, list):
        for server in servers:
            if not isinstance(server, dict):
                continue
            url = server.get("url")
            if isinstance(url, str):
                base_url = _absolute_http_url(url, source_url)
                if base_url:
                    return base_url

    host = document.get("host")
    if isinstance(host, str) and host.strip():
        schemes = document.get("schemes")
        scheme = "https"
        if isinstance(schemes, list) and schemes:
            scheme = str(schemes[0]).strip() or scheme
        base_path = document.get("basePath") if isinstance(document.get("basePath"), str) else ""
        return f"{scheme}://{host.strip()}{base_path}".rstrip("/")

    return None


def _common_first_path_segment(endpoints: list[dict]) -> str | None:
    segments: list[str] = []
    for endpoint in endpoints:
        path = endpoint.get("path")
        if not isinstance(path, str) or not path.startswith("/"):
            continue
        parts = [part for part in path.split("/") if part]
        if parts:
            segments.append(parts[0])
    if not segments:
        return None
    first = segments[0]
    if all(segment == first for segment in segments):
        return f"/{first}"
    return None


def _infer_proxy_prefix_rewrite(
    source_url: str,
    endpoints: list[dict] | None,
) -> dict[str, str] | None:
    """Infer common dev-proxy path rewrites from a Swagger document URL.

    RuoYi-style deployments often expose docs at `/api/v3/api-docs`, while the
    generated paths still contain a frontend dev proxy prefix like `/dev-api`.
    Executing those paths literally hits the SPA/nginx layer. In that shape the
    real public API prefix is the first segment from the docs URL (`/api`).
    """
    if not endpoints:
        return None
    parsed = urlparse(source_url)
    source_parts = [part for part in parsed.path.split("/") if part]
    if not source_parts:
        return None
    doc_prefix = f"/{source_parts[0]}"
    path_prefix = _common_first_path_segment(endpoints)
    if not path_prefix or path_prefix == doc_prefix:
        return None
    if path_prefix in {"/dev-api", "/api"} and doc_prefix in {"/api", "/dev-api"}:
        return {"from": path_prefix, "to": doc_prefix}
    return None


def _apply_path_prefix_rewrite(endpoints: list[dict], rewrite: dict[str, str] | None) -> list[dict]:
    if not rewrite:
        return endpoints
    source_prefix = rewrite.get("from", "").rstrip("/")
    target_prefix = rewrite.get("to", "").rstrip("/")
    if not source_prefix or not target_prefix:
        return endpoints

    rewritten: list[dict] = []
    for endpoint in endpoints:
        item = dict(endpoint)
        path = item.get("path")
        if isinstance(path, str) and (path == source_prefix or path.startswith(f"{source_prefix}/")):
            item["original_path"] = path
            item["path"] = f"{target_prefix}{path[len(source_prefix):]}" or target_prefix
        rewritten.append(item)
    return rewritten


def _is_json_or_yaml(text: str) -> bool:
    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        return True
    if stripped.startswith("openapi:") or stripped.startswith("swagger:"):
        return True
    return False


def classify_input(source: str) -> str:
    """Classify user input as swagger_url, swagger_json, swagger_yaml, or url."""
    stripped = source.strip()

    # Raw JSON/YAML content
    if stripped.startswith("{") or stripped.startswith("["):
        return "swagger_json"
    if stripped.startswith("openapi:") or stripped.startswith("swagger:"):
        return "swagger_yaml"

    # URL
    if stripped.startswith("http://") or stripped.startswith("https://"):
        if _looks_like_swagger_url(stripped):
            return "swagger_url"
        # Try fetching to check if it's a Swagger doc
        return "url"

    # Try parsing as JSON/YAML
    if _is_json_or_yaml(stripped):
        try:
            import json
            json.loads(stripped)
            return "swagger_json"
        except Exception:
            return "swagger_yaml"

    return "url"


async def run(state: AgentState) -> AgentState:
    install_tool_context(state)
    source = state.get("source_input", "")
    input_type = state.get("input_type", "unknown")

    if input_type == "unknown":
        input_type = classify_input(source)
        state["input_type"] = input_type

    document_content = None
    parsed_api_schema = None
    ui_seed_url = state.get("ui_seed_url")

    if input_type == "swagger_url":
        # Fetch Swagger document from URL
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(source)
                resp.raise_for_status()
                document_content = resp.text
        except Exception as e:
            logger.warning("Failed to fetch Swagger URL %s: %s", source, e)
            state["last_error"] = f"Failed to fetch Swagger URL: {e}"

    elif input_type in ("swagger_json", "swagger_yaml"):
        document_content = source

    elif input_type == "url":
        source_is_http_url = source.startswith(("http://", "https://"))
        if not ui_seed_url and source_is_http_url:
            ui_seed_url = source
        # Try to detect if the URL actually serves a Swagger doc
        if source_is_http_url:
            try:
                async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                    resp = await client.get(source)
                    content_type = resp.headers.get("content-type", "")
                    text = resp.text.strip()
                    if "json" in content_type or text.startswith("{"):
                        try:
                            import json
                            data = json.loads(text)
                            if isinstance(data, dict) and ("openapi" in data or "swagger" in data):
                                document_content = text
                                input_type = "swagger_url"
                                state["input_type"] = input_type
                                ui_seed_url = None
                        except Exception:
                            pass
            except Exception:
                pass

    state["document_content"] = document_content

    # Parse the document if we have content
    document_base_url = None
    if document_content:
        try:
            parsed_api_schema = parse_api_document_content(document_content)
            prefix_rewrite = _infer_proxy_prefix_rewrite(
                source,
                parsed_api_schema,
            ) if input_type == "swagger_url" else None
            parsed_api_schema = _apply_path_prefix_rewrite(parsed_api_schema, prefix_rewrite)
            state["parsed_api_schema"] = parsed_api_schema
            if prefix_rewrite:
                state["api_path_prefix_rewrite"] = prefix_rewrite
            document_base_url = _extract_document_base_url(
                document_content,
                source_url=source if input_type == "swagger_url" else None,
            )
        except Exception as e:
            logger.warning("Failed to parse API document: %s", e)
            state["parsed_api_schema"] = []

    # Fix target_url for Swagger URLs — strip the doc path to get the API base URL
    base_url_override = state.get("base_url_override")
    if input_type == "url" and ui_seed_url and source.startswith(("http://", "https://")):
        state["target_url"] = ui_seed_url.rstrip("/")

    if base_url_override and input_type in ("swagger_url", "swagger_json", "swagger_yaml"):
        state["target_url"] = base_url_override.rstrip("/")
        logger.info("Using base URL override: %s", base_url_override)
    elif input_type == "swagger_url":
        base_url = document_base_url or _extract_base_url(source)
        state["target_url"] = base_url
        logger.info("Extracted base URL from Swagger: %s", base_url)
    elif input_type in ("swagger_json", "swagger_yaml"):
        state["target_url"] = document_base_url or ""
        if document_base_url:
            logger.info("Extracted base URL from API document: %s", document_base_url)

    if ui_seed_url:
        state["ui_seed_url"] = ui_seed_url

    endpoint_count = len(parsed_api_schema) if parsed_api_schema else 0
    record_tool_call(
        state,
        tool_name="planner.parse_requirement",
        layer="planner",
        status="success",
        input_summary={
            "input_type": state.get("input_type"),
            "has_source": bool(source),
            "test_type": state.get("test_type"),
        },
        output_summary={
            "endpoint_count": endpoint_count,
            "target_url": state.get("target_url"),
            "ui_seed_url": state.get("ui_seed_url"),
        },
    )
    detail = f"input_type={state['input_type']}, endpoints={endpoint_count}"
    state.setdefault("workflow_steps", []).append(
        {"node": "source_loader", "status": "done", "detail": detail}
    )
    await persist_progress(state, "source_loader", "done", detail)
    return state

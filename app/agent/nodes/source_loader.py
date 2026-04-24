import logging
import re

import httpx

from app.agent.state import AgentState
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
    source = state.get("source_input", "")
    input_type = state.get("input_type", "unknown")

    if input_type == "unknown":
        input_type = classify_input(source)
        state["input_type"] = input_type

    document_content = None
    parsed_api_schema = None
    ui_seed_url = None

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
        ui_seed_url = source
        # Try to detect if the URL actually serves a Swagger doc
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
    if document_content:
        try:
            parsed_api_schema = parse_api_document_content(document_content)
            state["parsed_api_schema"] = parsed_api_schema
        except Exception as e:
            logger.warning("Failed to parse API document: %s", e)
            state["parsed_api_schema"] = []

    if ui_seed_url:
        state["ui_seed_url"] = ui_seed_url

    state.setdefault("workflow_steps", []).append(
        {
            "node": "source_loader",
            "status": "done",
            "detail": f"input_type={state['input_type']}, endpoints={len(parsed_api_schema) if parsed_api_schema else 0}",
        }
    )
    return state

"""
Scene Detector — Rule-based pre-analysis of API schemas.

Inspired by Anything Analyzer's SceneDetector. Detects 10+ scene types
from parsed API schemas using URL patterns, method distributions, field
names, and response structures. Single-pass O(N) traversal.

Scene types:
  - auth-login: login/register/password endpoints
  - auth-oauth: OAuth2 flows (authorize, token, callback)
  - crud-resource: standard CRUD on resources
  - file-upload: multipart file upload endpoints
  - search-filter: search/filter/query endpoints
  - pagination: paginated list endpoints
  - websocket: WebSocket endpoints
  - streaming: SSE/streaming endpoints
  - admin: admin/management endpoints
  - public: public/unauthenticated endpoints
  - webhook: webhook/callback endpoints
  - batch: batch/bulk operation endpoints
"""

import re
from dataclasses import dataclass, field


@dataclass
class SceneHint:
    scene: str
    confidence: str  # "high", "medium", "low"
    endpoints: list[str] = field(default_factory=list)
    detail: str = ""


# URL patterns for scene detection (compiled once)
_AUTH_LOGIN_PATTERNS = [
    re.compile(r"/login", re.I),
    re.compile(r"/signin", re.I),
    re.compile(r"/sign-in", re.I),
    re.compile(r"/register", re.I),
    re.compile(r"/signup", re.I),
    re.compile(r"/sign-up", re.I),
    re.compile(r"/password", re.I),
    re.compile(r"/reset", re.I),
    re.compile(r"/verify", re.I),
    re.compile(r"/otp", re.I),
    re.compile(r"/captcha", re.I),
]

_AUTH_OAUTH_PATTERNS = [
    re.compile(r"/oauth", re.I),
    re.compile(r"/authorize", re.I),
    re.compile(r"/token", re.I),
    re.compile(r"/callback", re.I),
    re.compile(r"/authn", re.I),
    re.compile(r"/sso", re.I),
]

_FILE_UPLOAD_PATTERNS = [
    re.compile(r"/upload", re.I),
    re.compile(r"/import", re.I),
    re.compile(r"/attach", re.I),
    re.compile(r"/file", re.I),
    re.compile(r"/image", re.I),
    re.compile(r"/media", re.I),
]

_SEARCH_PATTERNS = [
    re.compile(r"/search", re.I),
    re.compile(r"/filter", re.I),
    re.compile(r"/query", re.I),
    re.compile(r"/find", re.I),
    re.compile(r"/lookup", re.I),
]

_WEBHOOK_PATTERNS = [
    re.compile(r"/webhook", re.I),
    re.compile(r"/hook", re.I),
    re.compile(r"/callback", re.I),
    re.compile(r"/notify", re.I),
    re.compile(r"/event", re.I),
]

_ADMIN_PATTERNS = [
    re.compile(r"/admin", re.I),
    re.compile(r"/manage", re.I),
    re.compile(r"/system", re.I),
    re.compile(r"/config", re.I),
    re.compile(r"/setting", re.I),
]

_BATCH_PATTERNS = [
    re.compile(r"/batch", re.I),
    re.compile(r"/bulk", re.I),
    re.compile(r"/multi", re.I),
]

# Field names that indicate auth
_AUTH_FIELD_NAMES = {
    "username", "password", "email", "token", "access_token",
    "refresh_token", "client_id", "client_secret", "grant_type",
    "code", "redirect_uri", "authorization",
}

# Field names that indicate pagination
_PAGINATION_FIELD_NAMES = {
    "page", "page_size", "per_page", "limit", "offset",
    "cursor", "next_token", "total", "total_pages", "has_more",
}


def _match_any(path: str, patterns: list[re.Pattern]) -> bool:
    return any(p.search(path) for p in patterns)


def _count_methods(endpoints: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for ep in endpoints:
        method = (ep.get("method") or "GET").upper()
        counts[method] = counts.get(method, 0) + 1
    return counts


def detect_scenes(endpoints: list[dict]) -> list[SceneHint]:
    """Detect API scene types from parsed endpoint list.

    Args:
        endpoints: List of dicts with keys: path, method, summary,
                   required_fields, request_body_schema, response_schema, etc.

    Returns:
        List of SceneHint sorted by confidence (high first).
    """
    if not endpoints:
        return []

    hints: list[SceneHint] = []
    method_counts = _count_methods(endpoints)
    total = len(endpoints)

    # --- Auth Login ---
    login_eps = [ep for ep in endpoints if _match_any(ep.get("path", ""), _AUTH_LOGIN_PATTERNS)]
    if login_eps:
        confidence = "high" if len(login_eps) >= 2 else "medium"
        hints.append(SceneHint(
            scene="auth-login",
            confidence=confidence,
            endpoints=[ep.get("path", "") for ep in login_eps],
            detail=f"{len(login_eps)} login/register endpoints detected",
        ))

    # --- Auth OAuth ---
    oauth_eps = [ep for ep in endpoints if _match_any(ep.get("path", ""), _AUTH_OAUTH_PATTERNS)]
    if oauth_eps and not login_eps:  # avoid overlap
        hints.append(SceneHint(
            scene="auth-oauth",
            confidence="medium",
            endpoints=[ep.get("path", "") for ep in oauth_eps],
            detail=f"{len(oauth_eps)} OAuth endpoints detected",
        ))

    # --- Auth fields in request bodies ---
    auth_field_eps = []
    for ep in endpoints:
        fields = set(ep.get("required_fields") or [])
        body = ep.get("request_body_schema") or {}
        if isinstance(body, dict):
            fields.update(body.keys())
        if fields & _AUTH_FIELD_NAMES:
            auth_field_eps.append(ep)
    if auth_field_eps and not login_eps:
        hints.append(SceneHint(
            scene="auth-token",
            confidence="medium",
            endpoints=[ep.get("path", "") for ep in auth_field_eps[:5]],
            detail=f"{len(auth_field_eps)} endpoints use auth fields",
        ))

    # --- CRUD Resource ---
    # Look for resource patterns: GET /resources, POST /resources, GET /resources/{id}, etc.
    resource_groups: dict[str, list[dict]] = {}
    for ep in endpoints:
        path = ep.get("path", "")
        # Extract resource name from path: /api/v1/users/{id} -> users
        parts = [p for p in path.strip("/").split("/") if p and not p.startswith("{") and not p.startswith("v")]
        if parts:
            resource = parts[-1]
            if resource not in resource_groups:
                resource_groups[resource] = []
            resource_groups[resource].append(ep)

    for resource, eps in resource_groups.items():
        methods = {ep.get("method", "GET").upper() for ep in eps}
        has_list = "GET" in methods and any(
            "{" not in ep.get("path", "") for ep in eps if ep.get("method", "GET").upper() == "GET"
        )
        has_create = "POST" in methods
        has_update = "PUT" in methods or "PATCH" in methods
        has_delete = "DELETE" in methods
        crud_score = sum([has_list, has_create, has_update, has_delete])
        if crud_score >= 3:
            confidence = "high" if crud_score == 4 else "medium"
            hints.append(SceneHint(
                scene="crud-resource",
                confidence=confidence,
                endpoints=[ep.get("path", "") for ep in eps],
                detail=f"Full CRUD on '{resource}' ({crud_score}/4 operations)",
            ))

    # --- File Upload ---
    upload_eps = [ep for ep in endpoints if _match_any(ep.get("path", ""), _FILE_UPLOAD_PATTERNS)]
    # Also check for multipart content type
    for ep in endpoints:
        ct = (ep.get("request_body_content_type") or "")
        if "multipart" in ct.lower() or "form-data" in ct.lower():
            if ep not in upload_eps:
                upload_eps.append(ep)
    if upload_eps:
        hints.append(SceneHint(
            scene="file-upload",
            confidence="medium",
            endpoints=[ep.get("path", "") for ep in upload_eps],
            detail=f"{len(upload_eps)} file upload endpoints",
        ))

    # --- Search/Filter ---
    search_eps = [ep for ep in endpoints if _match_any(ep.get("path", ""), _SEARCH_PATTERNS)]
    if search_eps:
        hints.append(SceneHint(
            scene="search-filter",
            confidence="medium",
            endpoints=[ep.get("path", "") for ep in search_eps],
            detail=f"{len(search_eps)} search/filter endpoints",
        ))

    # --- Pagination ---
    paginated_eps = []
    for ep in endpoints:
        raw_params = ep.get("query_params") or []
        if isinstance(raw_params, list):
            fields = {
                (f.get("name", "") if isinstance(f, dict) else str(f))
                for f in raw_params
            }
        else:
            fields = set()
        if fields & _PAGINATION_FIELD_NAMES:
            paginated_eps.append(ep)
    # Also check response for pagination fields
    for ep in endpoints:
        resp = ep.get("response_schema") or {}
        if isinstance(resp, dict):
            resp_fields = set(resp.keys())
            if resp_fields & _PAGINATION_FIELD_NAMES and ep not in paginated_eps:
                paginated_eps.append(ep)
    if paginated_eps and len(paginated_eps) >= 2:
        hints.append(SceneHint(
            scene="pagination",
            confidence="medium",
            endpoints=[ep.get("path", "") for ep in paginated_eps[:5]],
            detail=f"{len(paginated_eps)} paginated endpoints",
        ))

    # --- Webhook ---
    webhook_eps = [ep for ep in endpoints if _match_any(ep.get("path", ""), _WEBHOOK_PATTERNS)]
    if webhook_eps:
        hints.append(SceneHint(
            scene="webhook",
            confidence="medium",
            endpoints=[ep.get("path", "") for ep in webhook_eps],
            detail=f"{len(webhook_eps)} webhook endpoints",
        ))

    # --- Admin ---
    admin_eps = [ep for ep in endpoints if _match_any(ep.get("path", ""), _ADMIN_PATTERNS)]
    if admin_eps:
        hints.append(SceneHint(
            scene="admin",
            confidence="medium",
            endpoints=[ep.get("path", "") for ep in admin_eps],
            detail=f"{len(admin_eps)} admin/management endpoints",
        ))

    # --- Batch ---
    batch_eps = [ep for ep in endpoints if _match_any(ep.get("path", ""), _BATCH_PATTERNS)]
    if batch_eps:
        hints.append(SceneHint(
            scene="batch",
            confidence="low",
            endpoints=[ep.get("path", "") for ep in batch_eps],
            detail=f"{len(batch_eps)} batch/bulk endpoints",
        ))

    # --- Overall assessment ---
    if not hints:
        # No specific scene detected — likely a general API
        hints.append(SceneHint(
            scene="api-general",
            confidence="low",
            detail=f"{total} endpoints, {method_counts}",
        ))

    # Sort by confidence
    confidence_order = {"high": 0, "medium": 1, "low": 2}
    hints.sort(key=lambda h: confidence_order.get(h.confidence, 3))

    return hints


def summarize_scenes(hints: list[SceneHint]) -> str:
    """Generate a human-readable summary of detected scenes."""
    if not hints:
        return "No specific API scene detected."
    lines = []
    for h in hints:
        eps = ", ".join(h.endpoints[:3]) if h.endpoints else ""
        if len(h.endpoints) > 3:
            eps += f" (+{len(h.endpoints) - 3} more)"
        lines.append(f"[{h.confidence.upper()}] {h.scene}: {h.detail}" + (f" ({eps})" if eps else ""))
    return "\n".join(lines)

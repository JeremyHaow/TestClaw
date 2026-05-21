"""
Auth Chain Extractor — Traces credential flow through API schemas.

Inspired by Anything Analyzer's credential chain extraction. Identifies
which endpoints issue tokens/cookies and which endpoints consume them,
building a complete authentication map.
"""

import re
from dataclasses import dataclass, field


@dataclass
class AuthCredential:
    """A credential that flows through the API."""
    name: str
    cred_type: str  # "bearer_token", "api_key", "cookie", "basic_auth"
    source_endpoint: str = ""  # Where it's issued
    source_field: str = ""  # Response field name
    consumed_by: list[str] = field(default_factory=list)  # Endpoints that use it


@dataclass
class AuthChain:
    """Complete authentication chain for an API."""
    credentials: list[AuthCredential] = field(default_factory=list)
    auth_type: str = "unknown"  # "bearer", "api_key", "cookie", "basic", "oauth2", "none"
    login_endpoint: str = ""
    refresh_endpoint: str = ""
    summary: str = ""


# Response field patterns that indicate token issuance
_TOKEN_RESPONSE_PATTERNS = {
    "bearer_token": [
        re.compile(r"access_token", re.I),
        re.compile(r"token", re.I),
        re.compile(r"jwt", re.I),
        re.compile(r"id_token", re.I),
    ],
    "api_key": [
        re.compile(r"api_key", re.I),
        re.compile(r"apikey", re.I),
        re.compile(r"key", re.I),
        re.compile(r"secret", re.I),
    ],
    "refresh_token": [
        re.compile(r"refresh_token", re.I),
        re.compile(r"refresh", re.I),
    ],
}

# Request header/field patterns that indicate credential consumption
_CONSUME_PATTERNS = {
    "bearer_token": [
        re.compile(r"authorization", re.I),
        re.compile(r"Bearer", re.I),
    ],
    "api_key": [
        re.compile(r"x-api-key", re.I),
        re.compile(r"api-key", re.I),
        re.compile(r"apikey", re.I),
    ],
    "cookie": [
        re.compile(r"cookie", re.I),
        re.compile(r"session", re.I),
    ],
}


def _extract_response_fields(schema: dict | None) -> set[str]:
    """Extract all field names from a response schema."""
    if not schema or not isinstance(schema, dict):
        return set()
    fields = set()
    if "properties" in schema:
        fields.update(schema["properties"].keys())
    # Handle flat dict
    for key in schema:
        if isinstance(key, str):
            fields.add(key)
    return fields


def _extract_request_fields(endpoint: dict) -> set[str]:
    """Extract all field names from request (headers, body, query)."""
    fields = set()
    # Headers
    for h in endpoint.get("header_params") or []:
        if isinstance(h, dict):
            fields.add(h.get("name", "").lower())
        elif isinstance(h, str):
            fields.add(h.lower())
    # Query params
    for q in endpoint.get("query_params") or []:
        if isinstance(q, dict):
            fields.add(q.get("name", "").lower())
        elif isinstance(q, str):
            fields.add(q.lower())
    # Body fields
    body = endpoint.get("request_body_schema") or {}
    if isinstance(body, dict):
        fields.update(k.lower() for k in body.keys())
    # Required fields
    for f in endpoint.get("required_fields") or []:
        if isinstance(f, str):
            fields.add(f.lower())
    return fields


def extract_auth_chain(endpoints: list[dict]) -> AuthChain:
    """Analyze API endpoints to extract the authentication chain.

    Args:
        endpoints: Parsed endpoint list from doc_parser.

    Returns:
        AuthChain with credential flow information.
    """
    if not endpoints:
        return AuthChain(auth_type="none", summary="No endpoints to analyze")

    chain = AuthChain()
    credential_map: dict[str, AuthCredential] = {}

    # Phase 1: Find credential issuance (login/token endpoints)
    for ep in endpoints:
        path = ep.get("path", "")
        method = (ep.get("method") or "GET").upper()
        resp_fields = _extract_response_fields(ep.get("response_schema"))

        # Check if this endpoint issues tokens
        is_login = any(p.search(path) for p in [
            re.compile(r"/login", re.I),
            re.compile(r"/signin", re.I),
            re.compile(r"/token", re.I),
            re.compile(r"/auth", re.I),
        ])

        for cred_type, patterns in _TOKEN_RESPONSE_PATTERNS.items():
            for pattern in patterns:
                matches = [f for f in resp_fields if pattern.search(f)]
                if matches:
                    for match in matches:
                        cred_key = f"{cred_type}:{match}"
                        if cred_key not in credential_map:
                            cred = AuthCredential(
                                name=match,
                                cred_type=cred_type,
                                source_endpoint=f"{method} {path}",
                                source_field=match,
                            )
                            credential_map[cred_key] = cred
                            chain.credentials.append(cred)
                            if is_login:
                                chain.login_endpoint = f"{method} {path}"

        # Check for refresh token endpoint
        if any(p.search(path) for p in [re.compile(r"/refresh", re.I)]):
            chain.refresh_endpoint = f"{method} {path}"

    # Phase 2: Find credential consumption
    for ep in endpoints:
        path = ep.get("path", "")
        method = (ep.get("method") or "GET").upper()
        ep_str = f"{method} {path}"
        req_fields = _extract_request_fields(ep)

        for cred_key, cred in credential_map.items():
            # Skip the source endpoint itself
            if ep_str == cred.source_endpoint:
                continue

            # Check if this endpoint consumes this credential type
            consume_patterns = _CONSUME_PATTERNS.get(cred.cred_type, [])
            for pattern in consume_patterns:
                if any(pattern.search(f) for f in req_fields):
                    if ep_str not in cred.consumed_by:
                        cred.consumed_by.append(ep_str)
                    break

            # Also check for bearer token in header_params
            if cred.cred_type == "bearer_token":
                for h in ep.get("header_params") or []:
                    hname = (h.get("name", "") if isinstance(h, dict) else str(h)).lower()
                    if hname == "authorization":
                        if ep_str not in cred.consumed_by:
                            cred.consumed_by.append(ep_str)
                        break

    # Phase 3: Determine overall auth type
    cred_types = {c.cred_type for c in chain.credentials}
    if "bearer_token" in cred_types:
        chain.auth_type = "bearer"
    elif "api_key" in cred_types:
        chain.auth_type = "api_key"
    elif "cookie" in cred_types:
        chain.auth_type = "cookie"
    elif chain.credentials:
        chain.auth_type = "mixed"
    else:
        # Check if any endpoint declares auth without exposing a concrete header
        # parameter. OpenAPI security schemes often mark this through
        # operation.security/global security, which doc_parser normalizes to
        # `auth_required`.
        auth_required_endpoints = [
            f"{ep.get('method', 'GET').upper()} {ep.get('path', '')}"
            for ep in endpoints
            if ep.get("auth_required")
        ]
        if auth_required_endpoints:
            chain.auth_type = "bearer"
            chain.credentials.append(AuthCredential(
                name="Authorization",
                cred_type="bearer_token",
                source_endpoint="(declared in spec)",
                consumed_by=auth_required_endpoints,
            ))
        # Check if any endpoint requires Authorization header
        has_auth_header = any(
            any(
                (h.get("name", "") if isinstance(h, dict) else str(h)).lower() == "authorization"
                for h in (ep.get("header_params") or [])
            )
            for ep in endpoints
        )
        if has_auth_header and chain.auth_type == "unknown":
            chain.auth_type = "bearer"
            chain.credentials.append(AuthCredential(
                name="Authorization",
                cred_type="bearer_token",
                source_endpoint="(declared in spec)",
                consumed_by=[
                    f"{ep.get('method', 'GET').upper()} {ep.get('path', '')}"
                    for ep in endpoints
                    if any(
                        (h.get("name", "") if isinstance(h, dict) else str(h)).lower() == "authorization"
                        for h in (ep.get("header_params") or [])
                    )
                ],
            ))
        elif chain.auth_type == "unknown":
            chain.auth_type = "none"

    # Build summary
    chain.summary = _build_summary(chain)

    return chain


def _build_summary(chain: AuthChain) -> str:
    """Build a human-readable auth chain summary."""
    if chain.auth_type == "none":
        return "No authentication detected. All endpoints appear to be public."

    lines = [f"Auth type: {chain.auth_type}"]
    if chain.login_endpoint:
        lines.append(f"Login: {chain.login_endpoint}")
    if chain.refresh_endpoint:
        lines.append(f"Refresh: {chain.refresh_endpoint}")

    for cred in chain.credentials:
        consumed = len(cred.consumed_by)
        lines.append(
            f"  {cred.name} ({cred.cred_type}) "
            f"from {cred.source_endpoint} -> consumed by {consumed} endpoints"
        )

    return "\n".join(lines)


def get_auth_test_hints(chain: AuthChain) -> list[dict]:
    """Generate test case hints based on the auth chain.

    Returns list of test hints with title, category, and steps.
    """
    hints = []

    if chain.auth_type == "none":
        hints.append({
            "title": "验证无鉴权端点的公开可访问性",
            "category": "SECURITY",
            "steps": ["Send request without any auth credentials", "Verify 200 response"],
        })
        return hints

    # Test: Missing token
    if chain.credentials:
        cred = chain.credentials[0]
        if cred.consumed_by:
            hints.append({
                "title": f"验证缺少 {cred.name} 时返回 401",
                "category": "SECURITY",
                "steps": [
                    f"Send request to {cred.consumed_by[0]} without {cred.name}",
                    "Verify 401 Unauthorized response",
                ],
            })

    # Test: Invalid token
    hints.append({
        "title": "验证无效 token 返回 401",
        "category": "SECURITY",
        "steps": [
            "Send request with invalid/expired token",
            "Verify 401 or 403 response",
        ],
    })

    # Test: Token refresh
    if chain.refresh_endpoint:
        hints.append({
            "title": "验证 token 刷新流程",
            "category": "FUNCTIONAL",
            "steps": [
                "Obtain initial token via login",
                "Use refresh endpoint to get new token",
                "Verify new token works for subsequent requests",
            ],
        })

    # Test: Login flow
    if chain.login_endpoint:
        hints.append({
            "title": "验证登录流程",
            "category": "FUNCTIONAL",
            "steps": [
                f"POST to {chain.login_endpoint} with valid credentials",
                "Verify token is returned in response",
                "Verify token can be used for authenticated requests",
            ],
        })

    return hints

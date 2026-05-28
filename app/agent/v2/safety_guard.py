"""Simplified safety guard for v2 agent architecture.

Three responsibilities only:
1. Schema validation -- check tool args against a JSON schema
2. Write-operation gating -- enforce execution policy on HTTP methods
3. Human-in-the-loop -- request approval for risky write operations

NO marker tuples. NO intent guessing. The LLM decides what to do;
this module only enforces schema correctness and execution policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.redaction import redact_sensitive_data

SAFE_HTTP_METHODS = {"GET", "HEAD", "OPTIONS"}
WRITE_HTTP_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass
class GuardResult:
    approved: bool
    blocked: bool = False
    requires_approval: bool = False
    block_reason: str | None = None
    approval_request: dict | None = None

    @classmethod
    def approved(cls) -> GuardResult:
        return cls(approved=True)

    @classmethod
    def deny(cls, reason: str) -> GuardResult:
        """Factory: a blocked/denied result with a human-readable reason."""
        return cls(approved=False, blocked=True, block_reason=reason)

    @classmethod
    def request_approval(cls, request: dict) -> GuardResult:
        """Factory: a result that requires human approval before execution."""
        return cls(approved=False, requires_approval=True, approval_request=request)


class SafetyGuard:
    """Simplified safety guard -- schema validation + policy gating + human-in-the-loop.

    NO marker tuples. NO intent guessing. The LLM decides what to do;
    we only enforce schema correctness and execution policy.
    """

    def __init__(self, execution_policy: str = "safe_read_only"):
        self.execution_policy = execution_policy

    def validate(
        self,
        tool_name: str,
        tool_args: dict,
        tool_schema: dict | None = None,
    ) -> GuardResult:
        """Validate a tool call from the LLM.

        Args:
            tool_name: Name of the tool the LLM wants to call.
            tool_args: Arguments the LLM provided.
            tool_schema: JSON schema for the tool (optional, for validation).

        Returns:
            GuardResult indicating approved / blocked / needs_approval.
        """
        # Layer 1: Schema validation (if schema provided)
        if tool_schema:
            schema_result = self._validate_schema(tool_args, tool_schema)
            if not schema_result["valid"]:
                return GuardResult.deny(
                    f"Schema validation failed: {schema_result['error']}"
                )

        # Layer 2: Write operation gating
        if tool_name == "api.http_request":
            return self._validate_http_request(tool_args)

        # Browser tools operate in the configured test browser.
        if tool_name in {"ui.playwright_cli", "ui.click", "ui.fill"}:
            return GuardResult.approved()

        # All other tools pass through
        return GuardResult.approved()

    # -- internal helpers --------------------------------------------------

    def _validate_http_request(self, args: dict) -> GuardResult:
        """Gate HTTP requests based on method and execution policy."""
        request = args.get("request") if isinstance(args.get("request"), dict) else args
        method = str(request.get("method") or "GET").upper()

        if method in SAFE_HTTP_METHODS:
            return GuardResult.approved()

        if self.execution_policy == "safe_read_only":
            return GuardResult.deny(
                f"safe_read_only policy blocks {method} requests. "
                "Ask the user to enable write operations."
            )

        if self.execution_policy in {"safe_with_auth", "write_allowed"}:
            url = request.get("url", "unknown")
            body_preview = redact_sensitive_data(request.get("body", ""))
            return GuardResult.request_approval({
                "action": f"{method} {url}",
                "method": method,
                "url": url,
                "risk_level": "high" if method == "DELETE" else "medium",
                "body_preview": str(body_preview)[:200],
            })

        return GuardResult.deny(
            f"Unknown execution policy: {self.execution_policy}"
        )

    @staticmethod
    def _validate_schema(args: dict, schema: dict) -> dict:
        """Validate tool arguments against a JSON schema.

        Simple validation -- check required fields and basic types.
        """
        required = schema.get("required", [])
        properties = schema.get("properties", {})

        for field in required:
            if field not in args:
                return {"valid": False, "error": f"Missing required field: {field}"}

        for field, value in args.items():
            if field in properties:
                expected_type = properties[field].get("type")
                if expected_type and not SafetyGuard._check_type(value, expected_type):
                    return {
                        "valid": False,
                        "error": (
                            f"Field '{field}' expected type {expected_type}, "
                            f"got {type(value).__name__}"
                        ),
                    }
                expected_enum = properties[field].get("enum")
                if expected_enum and value not in expected_enum:
                    return {
                        "valid": False,
                        "error": f"Field '{field}' expected one of {expected_enum}",
                    }

        if schema.get("additionalProperties") is False:
            allowed = set(properties)
            for field in args:
                if field not in allowed:
                    return {"valid": False, "error": f"Unexpected field: {field}"}

        return {"valid": True, "error": None}

    @staticmethod
    def _check_type(value: Any, expected_type: str | list[str]) -> bool:
        """Check if *value* matches the expected JSON schema type."""
        if isinstance(expected_type, list):
            return any(SafetyGuard._check_type(value, item) for item in expected_type)
        type_map: dict[str, type | tuple[type, ...]] = {
            "string": str,
            "number": (int, float),
            "integer": int,
            "boolean": bool,
            "array": list,
            "object": dict,
        }
        if expected_type == "null":
            return value is None
        expected = type_map.get(expected_type)
        if expected is None:
            return True  # unknown type -- pass
        return isinstance(value, expected)

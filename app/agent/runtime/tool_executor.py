from __future__ import annotations

import time
from typing import Any

from app.agent.runtime.failure_taxonomy import classify_api_failure, classify_ui_failure
from app.agent.runtime.models import ToolExecutionResult
from app.agent.runtime.policies import api_method_allowed, compact_runtime_value, redact_runtime_payload
from app.core.redaction import redact_sensitive_headers


class ToolExecutor:
    """Unified dispatch point for runtime v1 tool calls.

    Runners may still keep compatibility result shaping, but API/UI tool I/O
    should pass through this class so execution policy, redaction, and runtime
    observations stay consistent.
    """

    def __init__(self, state: dict[str, Any] | None = None) -> None:
        self.state = state if state is not None else {}

    async def execute(
        self,
        tool_name: str,
        inputs: dict[str, Any] | None = None,
        *,
        context: dict[str, Any] | None = None,
    ) -> ToolExecutionResult:
        payload = inputs or {}
        context = context or {}
        if tool_name == "api.http_request":
            return await self._execute_api_http_request(payload, context)
        if tool_name == "api.derive_schema_requests":
            return self._execute_api_derive_schema_requests(payload)
        if tool_name == "ui.playwright_cli":
            return await self._execute_playwright_cli(payload)
        if tool_name in {"memory.retrieve_rag_context", "memory.retrieve"}:
            return self._execute_memory_retrieve(payload)
        if tool_name == "human.ask":
            return self._execute_human_ask(payload)
        return ToolExecutionResult(
            tool_name=tool_name,
            layer="unknown",
            status="blocked",
            inputs=redact_runtime_payload(payload),
            outputs={"reason": f"Unknown runtime tool: {tool_name}"},
            error=f"Unknown runtime tool: {tool_name}",
        )

    async def _execute_api_http_request(
        self,
        inputs: dict[str, Any],
        context: dict[str, Any],
    ) -> ToolExecutionResult:
        req = inputs.get("request") if isinstance(inputs.get("request"), dict) else inputs
        method = str(req.get("method") or "GET").upper()
        policy = str(context.get("execution_policy") or self.state.get("api_execution_policy") or "safe_read_only")
        if not api_method_allowed(method, policy):
            return ToolExecutionResult(
                tool_name="api.http_request",
                layer="api",
                status="blocked",
                inputs=redact_runtime_payload(req),
                outputs={
                    "failure_type": "safe_write_blocked",
                    "safety_decision": "blocked_by_read_only_policy",
                    "execution_policy": policy,
                },
                error=f"{method} is blocked by execution policy {policy}",
            )

        client = context.get("client")
        if client is None:
            return ToolExecutionResult(
                tool_name="api.http_request",
                layer="api",
                status="blocked",
                inputs=redact_runtime_payload(req),
                outputs={"failure_type": "environment_blocked", "reason": "No HTTP client was provided."},
                error="No HTTP client was provided.",
            )

        retry_count = max(0, int(context.get("retry_count") or 0))
        request_budget = context.get("request_budget")
        attempts = max(1, retry_count + 1)
        if request_budget is not None:
            if int(request_budget) <= 0:
                return ToolExecutionResult(
                    tool_name="api.http_request",
                    layer="api",
                    status="blocked",
                    inputs=redact_runtime_payload(req),
                    outputs={"failure_type": "environment_blocked", "reason": "HTTP execution budget exhausted."},
                    error="HTTP execution budget exhausted",
                    raw={"response": None, "attempts": 0, "error": RuntimeError("HTTP execution budget exhausted")},
                )
            attempts = min(attempts, int(request_budget))

        started = time.perf_counter()
        last_exc: Exception | None = None
        response = None
        used_attempts = 0
        for attempt in range(1, attempts + 1):
            used_attempts = attempt
            try:
                response = await client.request(
                    method,
                    req["url"],
                    headers=req.get("headers") or None,
                    json=req.get("body"),
                    params=req.get("query_params"),
                )
                if response.status_code < 500 or attempt == attempts:
                    break
            except Exception as exc:
                last_exc = exc
                if attempt == attempts:
                    response = None

        elapsed = round((time.perf_counter() - started) * 1000, 2)
        if response is None:
            failure_type = classify_api_failure(error=last_exc)
            return ToolExecutionResult(
                tool_name="api.http_request",
                layer="api",
                status="failed",
                inputs={
                    "method": method,
                    "url": req.get("url"),
                    "headers": redact_sensitive_headers(req.get("headers") or {}),
                    "body": compact_runtime_value(req.get("body")),
                },
                outputs={"failure_type": failure_type, "error": str(last_exc or "HTTP request failed")[:300]},
                elapsed_ms=elapsed,
                error=str(last_exc or "HTTP request failed"),
                raw={"response": None, "attempts": used_attempts, "error": last_exc},
            )

        status = "success" if response.status_code < 400 else "failed"
        failure_type = classify_api_failure(status_code=response.status_code) if status == "failed" else None
        return ToolExecutionResult(
            tool_name="api.http_request",
            layer="api",
            status=status,
            inputs={
                "method": method,
                "url": req.get("url"),
                "headers": redact_sensitive_headers(req.get("headers") or {}),
                "body": compact_runtime_value(req.get("body")),
            },
            outputs={
                "status_code": response.status_code,
                "failure_type": failure_type,
                "attempts": used_attempts,
            },
            elapsed_ms=elapsed,
            raw={"response": response, "attempts": used_attempts, "error": None},
        )

    def _execute_api_derive_schema_requests(self, inputs: dict[str, Any]) -> ToolExecutionResult:
        schema = [item for item in self.state.get("parsed_api_schema") or [] if isinstance(item, dict)]
        method_policy = inputs.get("method_policy") if isinstance(inputs.get("method_policy"), dict) else {}
        allowed_methods = {str(method).upper() for method in method_policy.get("allowed_methods") or ["GET", "HEAD", "OPTIONS"]}
        include = inputs.get("include") if isinstance(inputs.get("include"), list) else []
        selected = []
        if include:
            wanted = {(str(item.get("method") or "").upper(), str(item.get("path") or "")) for item in include if isinstance(item, dict)}
            selected = [item for item in schema if (str(item.get("method") or "").upper(), str(item.get("path") or "")) in wanted]
        else:
            selected = [item for item in schema if str(item.get("method") or "GET").upper() in allowed_methods]
        return ToolExecutionResult(
            tool_name="api.derive_schema_requests",
            layer="api",
            status="success" if selected else "skipped",
            inputs=redact_runtime_payload(inputs),
            outputs={
                "candidate_total": len(schema),
                "selected_total": len(selected),
                "request_candidates": redact_runtime_payload(selected[:50]),
            },
        )

    async def _execute_playwright_cli(self, inputs: dict[str, Any]) -> ToolExecutionResult:
        from app.tools.playwright_tool import run_playwright_cli_command

        command = str(inputs.get("command") or "")
        started = time.perf_counter()
        result = await run_playwright_cli_command(command, session=str(inputs.get("session") or "default"))
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        output = {
            "status_code": result.get("status_code"),
            "stdout": str(result.get("stdout") or "")[:2000],
            "stderr": str(result.get("stderr") or "")[:800],
        }
        status = "success" if int(result.get("status_code") or 0) == 0 else "failed"
        failure_type = classify_ui_failure({**result, "passed": status == "success"})
        if failure_type:
            output["failure_type"] = failure_type
        evidence = []
        if command.startswith("snapshot") and result.get("stdout"):
            evidence.append({"kind": "ui_snapshot", "summary": str(result.get("stdout") or "")[:600]})
        return ToolExecutionResult(
            tool_name="ui.playwright_cli",
            layer="ui",
            status=status,
            inputs=redact_runtime_payload(inputs),
            outputs=redact_runtime_payload(output),
            evidence=evidence,
            elapsed_ms=elapsed,
            error=str(result.get("stderr") or "")[:800] if status == "failed" else None,
            raw=result,
        )

    def _execute_memory_retrieve(self, inputs: dict[str, Any]) -> ToolExecutionResult:
        retrieval = self.state.get("rag_retrieval") or {}
        return ToolExecutionResult(
            tool_name="memory.retrieve_rag_context",
            layer="memory",
            status=str(retrieval.get("status") or "skipped"),
            inputs=redact_runtime_payload(inputs),
            outputs=redact_runtime_payload(retrieval),
        )

    def _execute_human_ask(self, inputs: dict[str, Any]) -> ToolExecutionResult:
        question = str(inputs.get("question") or inputs.get("reason") or "Additional input is required.")
        return ToolExecutionResult(
            tool_name="human.ask",
            layer="supervisor",
            status="blocked",
            inputs=redact_runtime_payload(inputs),
            outputs={
                "question": question,
                "blocking": True,
                "requested_fields": inputs.get("requested_fields") or inputs.get("missing_fields") or [],
            },
        )

"""Core agent loop for v2 architecture.

LLM thinks -> picks tool -> safety guard validates -> executes -> feeds back.
Replaces the 14-node LangGraph graph with a single model-controlled loop
plus deterministic guardrails.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, AsyncIterator
from urllib.parse import urljoin

import httpx

from app.agent.action_runtime import append_agent_observation, append_agent_tool_call
from app.agent.progress import persist_progress
from app.agent.runtime.models import ToolExecutionResult
from app.agent.runtime.tool_executor import ToolExecutor
from app.agent.tool_registry import record_tool_call, v2_tool_capabilities
from app.agent.v2.approval import ApprovalChannel, ApprovalRequest
from app.agent.v2.config import AgentV2RuntimeConfig
from app.agent.v2.llm_bridge import (
    LLMBridge,
    ToolCall,
    build_messages,
    build_tools_schema,
    openai_tool_name,
)
from app.agent.v2.safety_guard import SafetyGuard
from app.core.redaction import redact_sensitive_data

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
你是 TestClaw 测试智能体。你的任务是根据用户的目标，使用提供的工具执行测试，产出带证据的报告。

## 你的能力
你可以：解析接口文档、发送 HTTP 请求、操作浏览器、截图、断言、生成报告。
你不能：直接修改用户的系统。所有写操作需要人确认。

## 工作流程
1. 理解用户目标（测试什么、怎么算成功）
2. 检查需要什么信息（接口文档？登录凭据？）→ 如果缺，用人工询问工具问
3. 制定测试策略（哪些端点、什么顺序、什么断言）
4. 执行测试（调用工具）
5. 观察结果，决定下一步（继续？replan？问人？）
6. 所有测试完成 → 调用 finish 工具，输出结构化报告

## 安全规则
- 默认只读：不执行 POST/PUT/PATCH/DELETE，除非用户明确允许
- 写操作需要人确认：调用请求审批工具，等用户回复
- 不要猜测：如果不确定，问用户
- 所有证据必须来自实际工具执行，不要编造

## 工具使用
- 优先使用 batch 工具减少调用次数
- 每次只调用一个工具，观察结果后再决定下一步
- 如果工具返回错误，分析原因并调整策略，不要重复同样的调用"""


# Tools that the agent loop handles internally (not dispatched to ToolExecutor).
_LOOP_INTERNAL_TOOLS = frozenset({"finish", "parse_openapi", "batch_http_get"})


class AgentLoop:
    """Core agent loop -- LLM thinks -> picks tool -> executes -> feeds back.

    Parameters
    ----------
    llm:
        LLM bridge for chat completions with tool calling.
    tool_executor:
        Unified dispatch point for runtime tool calls.
    safety_guard:
        Validates tool calls against schema and execution policy.
    state:
        Mutable agent state dict shared with progress persistence.
    """

    def __init__(
        self,
        llm: LLMBridge,
        tool_executor: ToolExecutor,
        safety_guard: SafetyGuard,
        state: dict[str, Any],
        approval_channel: ApprovalChannel | None = None,
        config: AgentV2RuntimeConfig | None = None,
    ) -> None:
        self.llm = llm
        self.tool_executor = tool_executor
        self.safety_guard = safety_guard
        self.state = state
        self.approval_channel = approval_channel
        if config is None:
            from app.config import settings

            from app.agent.v2.config import build_agent_v2_config

            config = build_agent_v2_config(settings)
        self.config = config
        self.messages: list[dict[str, Any]] = []
        self.tool_capabilities = v2_tool_capabilities()
        self.tool_schemas = build_tools_schema(self.tool_capabilities)
        self.tool_parameters_by_name = {
            capability.name: schema["function"]["parameters"]
            for capability, schema in zip(self.tool_capabilities, self.tool_schemas, strict=False)
        }
        self.tool_name_aliases = {
            openai_tool_name(capability.name): capability.name
            for capability in self.tool_capabilities
        }
        self.is_finished = False
        self.final_report: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, user_goal: str) -> AsyncIterator[dict[str, Any]]:
        """Run the agent loop.  Yields events for SSE streaming.

        Yields
        ------
        dict
            One of::

                {"type": "text", "content": "..."}
                {"type": "tool_call", "name": "...", "args": {...}, "result": {...}, "blocked": bool}
                {"type": "approval_needed", "request": {...}, "tool_call": {...}}
                {"type": "finished", "report": {...}}
        """
        self.messages = build_messages(SYSTEM_PROMPT, [], user_goal)

        for turn in range(self.config.max_turns):
            logger.info("Agent loop turn %d/%d", turn + 1, self.config.max_turns)

            # 1. LLM thinks + picks tool
            try:
                response = await self.llm.chat(
                    messages=self.messages,
                    tools=self.tool_schemas,
                    tool_choice="auto",
                    max_tokens=self.config.llm_max_tokens,
                )
            except Exception:
                logger.exception("LLM call failed on turn %d", turn + 1)
                yield {
                    "type": "finished",
                    "report": {
                        "verdict": "INCOMPLETE",
                        "reason": "LLM call failed",
                    },
                }
                return

            if response.tool_calls:
                for tool_call in response.tool_calls:
                    async for event in self._handle_tool_call(tool_call):
                        yield event
                        if self.is_finished:
                            return
            else:
                # LLM gave a text response (no tool call).
                yield {"type": "text", "content": response.content}
                self.messages.append(
                    {"role": "assistant", "content": response.content}
                )

            if self.is_finished:
                break

        # Max turns reached.
        yield {
            "type": "finished",
            "report": {
                "verdict": "INCOMPLETE",
                "reason": f"Reached max turns ({self.config.max_turns})",
            },
        }

    # ------------------------------------------------------------------
    # Tool call handling
    # ------------------------------------------------------------------

    async def _handle_tool_call(
        self, tool_call: ToolCall
    ) -> AsyncIterator[dict[str, Any]]:
        """Validate, execute, and record a single tool call."""
        canonical_name = self.tool_name_aliases.get(tool_call.name, tool_call.name)
        if canonical_name != tool_call.name:
            tool_call = ToolCall(id=tool_call.id, name=canonical_name, args=tool_call.args)

        tool_schema = self.tool_parameters_by_name.get(tool_call.name)
        if not tool_schema:
            tool_result = {"error": f"Unknown v2 tool: {tool_call.name}", "status": "blocked"}
            self._append_tool_messages(tool_call, tool_result)
            self._record_tool_call(tool_call, tool_result, layer="safety")
            await self._persist_progress(tool_call, tool_result)
            yield {
                "type": "tool_call",
                "name": tool_call.name,
                "args": redact_sensitive_data(tool_call.args),
                "result": redact_sensitive_data(tool_result),
                "blocked": True,
            }
            return

        # 2. Safety guard validates
        guard_result = self.safety_guard.validate(
            tool_call.name, tool_call.args, tool_schema
        )

        if guard_result.requires_approval:
            # Build an approval request from the guard result.
            approval_info = guard_result.approval_request or {}
            approval_request = ApprovalRequest(
                request_id=str(uuid.uuid4()),
                action=approval_info.get("action", ""),
                method=approval_info.get("method", ""),
                url=approval_info.get("url", ""),
                risk_level=approval_info.get("risk_level", "medium"),
                body_preview=approval_info.get("body_preview", ""),
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                tool_args=tool_call.args,
            )

            # Yield approval-needed event so SSE can surface it.
            yield {
                "type": "approval_needed",
                "request_id": approval_request.request_id,
                "request": approval_request.to_dict(),
                "tool_call": {
                    "id": tool_call.id,
                    "name": tool_call.name,
                    "args": redact_sensitive_data(tool_call.args),
                },
            }

            # Persist an approval-needed progress step.
            await self._persist_progress(
                tool_call,
                {
                    "status": "waiting_approval",
                    "outputs": approval_request.to_dict(),
                },
            )

            if self.approval_channel:
                # Wait for the user to approve or deny.
                approved = await self.approval_channel.request_approval(
                    approval_request
                )
                if approved:
                    tool_result = await self._execute_tool(tool_call)
                else:
                    reason = approval_request.response_message or "denied"
                    tool_result = {
                        "error": f"User denied: {reason}",
                        "status": "denied",
                    }
            else:
                # No channel configured -- deny with a clear message.
                tool_result = {
                    "error": (
                        "Write operation requires user approval "
                        "(no approval channel configured)"
                    ),
                    "status": "denied",
                }

            self._append_tool_messages(tool_call, tool_result)
            self._record_tool_call(tool_call, tool_result, layer="approval")
            await self._persist_progress(tool_call, tool_result)
            yield {
                "type": "tool_call",
                "name": tool_call.name,
                "args": redact_sensitive_data(tool_call.args),
                "result": redact_sensitive_data(tool_result),
                "approval_request_id": approval_request.request_id,
            }
            return

        if guard_result.blocked:
            tool_result = {"error": guard_result.block_reason, "status": "blocked"}
            self._append_tool_messages(tool_call, tool_result)
            self._record_tool_call(tool_call, tool_result, layer="safety")
            await self._persist_progress(tool_call, tool_result)
            yield {
                "type": "tool_call",
                "name": tool_call.name,
                "args": redact_sensitive_data(tool_call.args),
                "result": redact_sensitive_data(tool_result),
                "blocked": True,
            }
            return

        # 3. Execute tool
        tool_result = await self._execute_tool(tool_call)

        # 4. Feed result back to LLM
        self._append_tool_messages(tool_call, tool_result)

        # 5. Persist progress and record tool call
        self._record_tool_call(tool_call, tool_result)
        await self._persist_progress(tool_call, tool_result)

        # 6. Yield event
        yield {
            "type": "tool_call",
            "name": tool_call.name,
            "args": redact_sensitive_data(tool_call.args),
            "result": redact_sensitive_data(tool_result),
        }

        # Check for finish
        if tool_call.name == "finish":
            self.is_finished = True
            self.final_report = tool_result
            yield {"type": "finished", "report": tool_result}

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    async def _execute_tool(self, tool_call: ToolCall) -> dict[str, Any]:
        """Execute a tool, routing loop-internal tools locally."""
        if tool_call.name in _LOOP_INTERNAL_TOOLS:
            return await self._execute_internal_tool(tool_call)

        try:
            context = {
                "execution_policy": self.state.get("api_execution_policy") or "safe_read_only",
                "retry_count": self.config.api_request_retry_count,
            }
            if tool_call.name == "api.http_request":
                async with httpx.AsyncClient(
                    timeout=self.config.api_request_timeout_seconds
                ) as client:
                    result = await self.tool_executor.execute(
                        tool_name=tool_call.name,
                        inputs=tool_call.args,
                        context={**context, "client": client},
                    )
            else:
                result = await self.tool_executor.execute(
                    tool_name=tool_call.name,
                    inputs=tool_call.args,
                    context=context,
                )
            return {
                "status": result.status,
                "tool_name": result.tool_name,
                "layer": result.layer,
                "outputs": result.outputs,
                "error": result.error,
                "elapsed_ms": result.elapsed_ms,
            }
        except Exception:
            logger.exception("Tool execution failed: %s", tool_call.name)
            return {"error": "Tool execution failed", "status": "failed"}

    async def _execute_internal_tool(self, tool_call: ToolCall) -> dict[str, Any]:
        """Handle tools that live inside the agent loop itself."""
        if tool_call.name == "finish":
            return {
                "status": "success",
                "tool_name": "finish",
                "layer": "reporter",
                "verdict": tool_call.args.get("verdict", "PASS"),
                "summary": tool_call.args.get("summary", ""),
                "findings": tool_call.args.get("findings", []),
                "recommendations": tool_call.args.get("recommendations", []),
            }

        if tool_call.name == "parse_openapi":
            return await self._handle_parse_openapi(tool_call.args)

        if tool_call.name == "batch_http_get":
            return await self._handle_batch_http_get(tool_call.args)

        return {"error": f"Unknown internal tool: {tool_call.name}", "status": "failed"}

    # ------------------------------------------------------------------
    # Internal tool implementations
    # ------------------------------------------------------------------

    async def _handle_parse_openapi(self, args: dict[str, Any]) -> dict[str, Any]:
        """Parse an OpenAPI/Swagger document from URL or raw content."""
        source = str(args.get("source", "")).strip()
        if not source:
            return {"status": "failed", "error": "source is required"}

        try:
            # Fetch content if source is a URL
            if source.startswith(("http://", "https://")):
                async with httpx.AsyncClient(
                    timeout=self.config.openapi_fetch_timeout_seconds
                ) as client:
                    resp = await client.get(source)
                    resp.raise_for_status()
                    content = resp.text
            else:
                content = source

            # Parse endpoints
            from app.tools.doc_parser import parse_api_document_content

            endpoints = parse_api_document_content(content)

            # Extract base URL
            from app.agent.nodes.source_loader import _extract_document_base_url

            base_url = _extract_document_base_url(
                content,
                source_url=source if source.startswith(("http://", "https://")) else None,
            )

            # Store in agent state
            self.state["parsed_api_schema"] = endpoints
            self.state["document_content"] = content
            if base_url:
                self.state["target_url"] = base_url

            return {
                "status": "success",
                "outputs": {
                    "endpoint_count": len(endpoints),
                    "base_url": base_url,
                    "auth_required_count": sum(
                        1 for ep in endpoints if ep.get("auth_required")
                    ),
                },
            }
        except Exception as e:
            logger.warning("parse_openapi failed: %s", e)
            return {"status": "failed", "error": str(e)[:500]}

    async def _handle_batch_http_get(self, args: dict[str, Any]) -> dict[str, Any]:
        """Execute multiple GET requests in sequence."""
        endpoints_raw = args.get("endpoints", [])
        if not isinstance(endpoints_raw, list) or not endpoints_raw:
            return {"status": "failed", "error": "endpoints must be a non-empty array"}

        headers = args.get("headers") if isinstance(args.get("headers"), dict) else {}
        assert_status = int(args.get("assert_status", 200))
        base_url = str(
            args.get("base_url")
            or self.state.get("base_url_override")
            or self.state.get("target_url")
            or ""
        ).strip()
        endpoints = endpoints_raw[: self.config.batch_http_get_limit]
        skipped_overflow = max(0, len(endpoints_raw) - len(endpoints))

        results: list[dict[str, Any]] = []
        passed = 0
        failed = 0
        skipped = skipped_overflow

        context = {
            "execution_policy": self.state.get("api_execution_policy") or "safe_read_only",
            "retry_count": self.config.api_request_retry_count,
        }
        async with httpx.AsyncClient(timeout=self.config.api_request_timeout_seconds) as client:
            context["client"] = client
            for endpoint in endpoints:
                endpoint_url = str(endpoint) if endpoint else ""
                if endpoint_url and base_url and not endpoint_url.startswith(("http://", "https://")):
                    endpoint_url = urljoin(base_url.rstrip("/") + "/", endpoint_url.lstrip("/"))
                if not endpoint_url:
                    skipped += 1
                    results.append({
                        "endpoint": endpoint,
                        "status_code": None,
                        "passed": False,
                        "error": "empty endpoint",
                    })
                    continue

                try:
                    result: ToolExecutionResult = await self.tool_executor.execute(
                        "api.http_request",
                        {"method": "GET", "url": endpoint_url, "headers": headers},
                        context=context,
                    )
                    status_code = result.outputs.get("status_code") if isinstance(result.outputs, dict) else None
                    ok = status_code == assert_status if status_code is not None else False
                    if ok:
                        passed += 1
                    else:
                        failed += 1
                    results.append({
                        "endpoint": endpoint_url,
                        "status_code": status_code,
                        "passed": ok,
                        "error": result.error,
                    })
                except Exception as e:
                    failed += 1
                    results.append({
                        "endpoint": endpoint_url,
                        "status_code": None,
                        "passed": False,
                        "error": str(e)[:300],
                    })

        return {
            "status": "success" if failed == 0 and skipped == 0 else "partial",
            "outputs": {
                "total": len(endpoints_raw),
                "passed": passed,
                "failed": failed,
                "skipped": skipped,
                "skipped_overflow": skipped_overflow,
                "results": results,
            },
        }

    # ------------------------------------------------------------------
    # Message management
    # ------------------------------------------------------------------

    def _append_tool_messages(
        self, tool_call: ToolCall, tool_result: dict[str, Any]
    ) -> None:
        """Append assistant tool-call message and tool-result message."""
        self.messages.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": openai_tool_name(tool_call.name),
                            "arguments": json.dumps(tool_call.args, default=str),
                        },
                    }
                ],
            }
        )
        self.messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(tool_result, default=str),
            }
        )

    # ------------------------------------------------------------------
    # Progress & tool-call recording
    # ------------------------------------------------------------------

    async def _persist_progress(
        self, tool_call: ToolCall, result: dict[str, Any]
    ) -> None:
        """Persist progress to database for SSE streaming."""
        try:
            await persist_progress(
                self.state,
                node=f"agent_loop.{tool_call.name}",
                status=result.get("status", "unknown"),
                detail=json.dumps(result, default=str)[:500],
            )
        except Exception:
            logger.warning(
                "Failed to persist progress for %s", tool_call.name, exc_info=True
            )

    def _record_tool_call(
        self,
        tool_call: ToolCall,
        result: dict[str, Any],
        layer: str = "agent_loop",
    ) -> None:
        """Record a tool call into the agent state for react trace / reporting."""
        try:
            output_summary = (
                result.get("outputs") if isinstance(result.get("outputs"), dict) else result
            )
            status = str(result.get("status") or "unknown")
            protocol_layer = str(
                result.get("layer") or self._tool_layer(tool_call.name, layer)
            )
            record_tool_call(
                self.state,
                tool_name=tool_call.name,
                layer=layer,
                status=status,
                input_summary=tool_call.args,
                output_summary=output_summary,
                elapsed_ms=result.get("elapsed_ms"),
            )
            protocol_tool_call_id = append_agent_tool_call(
                self.state,
                tool_name=tool_call.name,
                layer=protocol_layer,
                status=status,
                inputs=tool_call.args,
                outputs=(
                    output_summary
                    if isinstance(output_summary, dict)
                    else {"value": output_summary}
                ),
                elapsed_ms=(
                    float(result["elapsed_ms"])
                    if result.get("elapsed_ms") is not None
                    else None
                ),
            )
            append_agent_observation(
                self.state,
                stage="agent_loop",
                layer=protocol_layer,
                tool_name=tool_call.name,
                status=status,
                summary=self._tool_result_summary(tool_call.name, result),
                failure_type=self._failure_type(result),
                inputs=tool_call.args,
                outputs=(
                    output_summary
                    if isinstance(output_summary, dict)
                    else {"value": output_summary}
                ),
                tool_call_ids=[protocol_tool_call_id],
                metadata={"source": "v2_agent_loop"},
            )
        except Exception:
            logger.warning(
                "Failed to record tool call for %s", tool_call.name, exc_info=True
            )

    def _tool_layer(self, tool_name: str, fallback: str) -> str:
        for capability in self.tool_capabilities:
            if capability.name == tool_name:
                return capability.layer
        return fallback

    @staticmethod
    def _failure_type(result: dict[str, Any]) -> str | None:
        outputs = result.get("outputs")
        if isinstance(outputs, dict) and outputs.get("failure_type"):
            return str(outputs["failure_type"])
        return None

    @staticmethod
    def _tool_result_summary(tool_name: str, result: dict[str, Any]) -> str:
        status = str(result.get("status") or "unknown")
        error = result.get("error")
        if error:
            return f"{tool_name}: {status} ({str(error)[:180]})"
        outputs = result.get("outputs")
        if isinstance(outputs, dict) and outputs.get("status_code") is not None:
            return f"{tool_name}: {status} status={outputs.get('status_code')}"
        return f"{tool_name}: {status}"

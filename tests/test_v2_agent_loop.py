"""Regression tests for v2 agent loop tool contracts and execution plumbing."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.agent.runtime.models import ToolExecutionResult
from app.agent.progress import determine_final_status
from app.agent.tool_registry import v2_tool_capabilities
from app.agent.v2.agent_loop import AgentLoop
from app.agent.v2.config import AgentV2RuntimeConfig
from app.agent.v2.llm_bridge import ToolCall, build_tools_schema, openai_tool_name
from app.agent.v2.safety_guard import SafetyGuard
from app.models.task import TaskStatus


def _config(**overrides: Any) -> AgentV2RuntimeConfig:
    values = {
        "max_turns": 3,
        "llm_timeout_seconds": 1.0,
        "llm_max_tokens": 512,
        "llm_retry_count": 0,
        "openapi_fetch_timeout_seconds": 1.0,
        "batch_http_get_limit": 2,
        "approval_timeout_seconds": 1.0,
        "approval_poll_interval_seconds": 0.01,
        "api_request_timeout_seconds": 1.0,
        "api_request_retry_count": 0,
    }
    values.update(overrides)
    return AgentV2RuntimeConfig(**values)


class DummyLLM:
    async def chat(self, **_: Any) -> Any:
        raise AssertionError("LLM should not be called by these unit tests")


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute(
        self,
        tool_name: str,
        inputs: dict[str, Any] | None = None,
        *,
        context: dict[str, Any] | None = None,
    ) -> ToolExecutionResult:
        self.calls.append(
            {
                "tool_name": tool_name,
                "inputs": inputs or {},
                "context": context or {},
            }
        )
        return ToolExecutionResult(
            tool_name=tool_name,
            layer="api" if tool_name.startswith("api.") else "unknown",
            status="success",
            inputs=inputs or {},
            outputs={"status_code": 200},
        )


def test_v2_tool_schema_includes_finish_and_excludes_legacy_decision_tools() -> None:
    tool_names = {tool.name for tool in v2_tool_capabilities()}
    assert "finish" in tool_names
    assert "api.http_request" in tool_names
    assert "parse_openapi" in tool_names
    assert "batch_http_get" in tool_names

    forbidden_prefixes = ("planner.", "agent.", "evidence.", "reporter.")
    assert not any(name.startswith(forbidden_prefixes) for name in tool_names)

    schema_names = {item["function"]["name"] for item in build_tools_schema(v2_tool_capabilities())}
    assert {openai_tool_name(name) for name in tool_names} == schema_names
    assert "api_http_request" in schema_names


def test_v2_final_report_verdict_sets_task_status() -> None:
    assert determine_final_status({"final_report": {"verdict": "PASS"}}) == TaskStatus.SUCCEEDED
    assert determine_final_status({"final_report": {"verdict": "BUG_FOUND"}}) == TaskStatus.BUG_FOUND
    assert determine_final_status({"final_report": {"verdict": "FAIL"}}) == TaskStatus.FAILED


def test_safety_guard_gates_real_api_http_request_tool_name() -> None:
    schemas = {
        tool.name: item["function"]["parameters"]
        for tool, item in zip(v2_tool_capabilities(), build_tools_schema(v2_tool_capabilities()), strict=False)
    }

    read_only_result = SafetyGuard("safe_read_only").validate(
        "api.http_request",
        {"method": "POST", "url": "https://api.example.test/items", "headers": {}, "body": {}},
        schemas["api.http_request"],
    )
    assert read_only_result.blocked is True

    approval_result = SafetyGuard("write_allowed").validate(
        "api.http_request",
        {"method": "POST", "url": "https://api.example.test/items", "headers": {}, "body": {}},
        schemas["api.http_request"],
    )
    assert approval_result.requires_approval is True
    assert approval_result.approval_request["method"] == "POST"


@pytest.mark.asyncio
async def test_agent_loop_supplies_http_client_context_to_api_request() -> None:
    executor = RecordingExecutor()
    loop = AgentLoop(
        llm=DummyLLM(),
        tool_executor=executor,  # type: ignore[arg-type]
        safety_guard=SafetyGuard("safe_read_only"),
        state={"api_execution_policy": "safe_read_only"},
        config=_config(),
    )

    result = await loop._execute_tool(
        ToolCall(
            id="call_1",
            name="api.http_request",
            args={"method": "GET", "url": "https://api.example.test/health", "headers": {}, "body": {}},
        )
    )

    assert result["status"] == "success"
    assert executor.calls[0]["context"]["client"] is not None
    assert executor.calls[0]["context"]["execution_policy"] == "safe_read_only"


@pytest.mark.asyncio
async def test_batch_http_get_uses_base_url_and_limit() -> None:
    executor = RecordingExecutor()
    loop = AgentLoop(
        llm=DummyLLM(),
        tool_executor=executor,  # type: ignore[arg-type]
        safety_guard=SafetyGuard("safe_read_only"),
        state={"target_url": "https://api.example.test/v1"},
        config=_config(batch_http_get_limit=2),
    )

    result = await loop._handle_batch_http_get(
        {
            "endpoints": ["/health", "users", "/overflow"],
            "assert_status": 200,
            "headers": {},
        }
    )

    assert result["status"] == "partial"
    assert result["outputs"]["total"] == 3
    assert result["outputs"]["skipped_overflow"] == 1
    assert [call["inputs"]["url"] for call in executor.calls] == [
        "https://api.example.test/v1/health",
        "https://api.example.test/v1/users",
    ]


@pytest.mark.asyncio
async def test_unknown_v2_tool_call_is_blocked() -> None:
    loop = AgentLoop(
        llm=DummyLLM(),
        tool_executor=RecordingExecutor(),  # type: ignore[arg-type]
        safety_guard=SafetyGuard("safe_read_only"),
        state={},
        config=_config(),
    )

    events = [
        event
        async for event in loop._handle_tool_call(
            ToolCall(id="call_1", name="planner_generate_test_cases", args={})
        )
    ]

    assert events[0]["blocked"] is True
    assert "Unknown v2 tool" in events[0]["result"]["error"]


@pytest.mark.asyncio
async def test_openai_tool_alias_maps_back_to_runtime_tool_name() -> None:
    executor = RecordingExecutor()
    loop = AgentLoop(
        llm=DummyLLM(),
        tool_executor=executor,  # type: ignore[arg-type]
        safety_guard=SafetyGuard("safe_read_only"),
        state={"api_execution_policy": "safe_read_only"},
        config=_config(),
    )

    events = [
        event
        async for event in loop._handle_tool_call(
            ToolCall(
                id="call_1",
                name="api_http_request",
                args={"method": "GET", "url": "https://api.example.test/health"},
            )
        )
    ]

    assert events[0]["name"] == "api.http_request"
    assert executor.calls[0]["tool_name"] == "api.http_request"
    assert loop.messages[0]["tool_calls"][0]["function"]["name"] == "api_http_request"


@pytest.mark.asyncio
async def test_v2_tool_events_redact_approval_args() -> None:
    loop = AgentLoop(
        llm=DummyLLM(),
        tool_executor=RecordingExecutor(),  # type: ignore[arg-type]
        safety_guard=SafetyGuard("write_allowed"),
        state={"api_execution_policy": "write_allowed"},
        config=_config(),
    )

    events = [
        event
        async for event in loop._handle_tool_call(
            ToolCall(
                id="call_1",
                name="api_http_request",
                args={
                    "method": "POST",
                    "url": "https://api.example.test/login",
                    "headers": {"Authorization": "Bearer secret-token"},
                    "body": {"username": "admin", "password": "secret-password"},
                },
            )
        )
    ]

    serialized = json.dumps(events, ensure_ascii=False)
    assert events[0]["type"] == "approval_needed"
    assert "secret-token" not in serialized
    assert "secret-password" not in serialized


@pytest.mark.asyncio
async def test_v2_tool_calls_are_recorded_for_runtime_event_store() -> None:
    loop = AgentLoop(
        llm=DummyLLM(),
        tool_executor=RecordingExecutor(),  # type: ignore[arg-type]
        safety_guard=SafetyGuard("safe_read_only"),
        state={"api_execution_policy": "safe_read_only"},
        config=_config(),
    )

    events = [
        event
        async for event in loop._handle_tool_call(
            ToolCall(
                id="call_1",
                name="api_http_request",
                args={"method": "GET", "url": "https://api.example.test/health"},
            )
        )
    ]

    assert events[0]["type"] == "tool_call"
    assert loop.state["tool_calls"][0]["tool"] == "api.http_request"
    assert loop.state["agent_tool_calls"][0]["tool_name"] == "api.http_request"
    assert loop.state["agent_observations"][0]["tool_call_ids"] == [
        loop.state["agent_tool_calls"][0]["tool_call_id"]
    ]

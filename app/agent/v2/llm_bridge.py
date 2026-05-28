"""Unified LLM calling interface using the OpenAI SDK directly.

This module provides a clean bridge to OpenAI-compatible LLMs for function
calling without LangChain dependencies.  It is the foundation for the v2
agent architecture where the model controls strategy and tool selection while
local code validates and executes.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion, ChatCompletionMessageToolCall

from app.agent.tool_registry import ToolCapability, _strict_object_schema

logger = logging.getLogger(__name__)
_OPENAI_TOOL_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]+")

# ---------------------------------------------------------------------------
# Response data classes
# ---------------------------------------------------------------------------


@dataclass
class ToolCall:
    """Parsed tool call from an LLM response."""

    id: str
    name: str
    args: dict[str, Any]


@dataclass
class LLMResponse:
    """Parsed LLM response with optional tool calls."""

    content: str | None
    tool_calls: list[ToolCall]
    raw: ChatCompletion


# ---------------------------------------------------------------------------
# Core bridge
# ---------------------------------------------------------------------------

_DEFAULT_TIMEOUT_SECONDS = 60.0
_DEFAULT_MAX_RETRIES = 1


class LLMBridge:
    """Unified LLM calling interface using the OpenAI SDK.

    Parameters
    ----------
    model:
        Model identifier (e.g. ``"gpt-4o"``).
    api_key:
        OpenAI-compatible API key.
    base_url:
        Optional custom base URL for OpenAI-compatible providers.
    timeout:
        Request timeout in seconds.
    """

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = _DEFAULT_MAX_RETRIES,
    ) -> None:
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self.model = model
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Call the LLM with messages and optional tools.

        Returns an ``LLMResponse`` containing either ``content`` (text reply)
        or ``tool_calls`` (function-call reply).  Includes one automatic retry
        on transient errors.
        """
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                completion: ChatCompletion = await self.client.chat.completions.create(**kwargs)
                return _parse_completion(completion)
            except Exception as exc:
                last_error = exc
                if attempt < self.max_retries and _is_transient_error(exc):
                    logger.warning(
                        "LLM call attempt %d failed (%s), retrying...",
                        attempt + 1,
                        exc,
                    )
                    continue
                raise
        # Should not reach here, but satisfy the type checker.
        raise last_error  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def _parse_completion(completion: ChatCompletion) -> LLMResponse:
    """Convert a raw ``ChatCompletion`` into an ``LLMResponse``."""
    message = completion.choices[0].message
    parsed_calls: list[ToolCall] = []

    if message.tool_calls:
        for tc in message.tool_calls:
            parsed_calls.append(_parse_tool_call(tc))

    return LLMResponse(
        content=message.content,
        tool_calls=parsed_calls,
        raw=completion,
    )


def _parse_tool_call(tc: ChatCompletionMessageToolCall) -> ToolCall:
    """Parse a single tool call, safely handling JSON argument errors."""
    try:
        args: dict[str, Any] = json.loads(tc.function.arguments)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse tool call arguments: %s", tc.function.arguments)
        args = {}
    return ToolCall(id=tc.id, name=tc.function.name, args=args)


def _is_transient_error(exc: Exception) -> bool:
    """Heuristic: retry on connection/timeout/rate-limit errors."""
    error_text = str(exc).lower()
    transient_markers = (
        "timeout",
        "timed out",
        "connection",
        "rate limit",
        "429",
        "500",
        "502",
        "503",
        "504",
    )
    return any(marker in error_text for marker in transient_markers)


# ---------------------------------------------------------------------------
# Tool schema helpers
# ---------------------------------------------------------------------------


def build_tools_schema(tools: list[ToolCapability]) -> list[dict[str, Any]]:
    """Convert ``ToolCapability`` objects to OpenAI function-calling schema.

    Each entry follows the format::

        {
            "type": "function",
            "function": {
                "name": "...",
                "description": "...",
                "parameters": { ... }  # strict JSON Schema
            }
        }
    """
    schema: list[dict[str, Any]] = []
    for tool in tools:
        parameters = _strict_object_schema(tool.input_schema)
        schema.append(
            {
                "type": "function",
                "function": {
                    "name": openai_tool_name(tool.name),
                    "description": tool.description,
                    "parameters": parameters,
                },
            }
        )
    return schema


def openai_tool_name(tool_name: str) -> str:
    """Convert internal dotted tool names to OpenAI-compatible function names."""
    normalized = _OPENAI_TOOL_NAME_RE.sub("_", str(tool_name or "").strip()).strip("_")
    return (normalized or "tool")[:64]


# ---------------------------------------------------------------------------
# Message helpers
# ---------------------------------------------------------------------------


def build_messages(
    system_prompt: str,
    history: list[dict[str, Any]],
    user_message: str,
) -> list[dict[str, Any]]:
    """Build an OpenAI-compatible message list.

    Parameters
    ----------
    system_prompt:
        The system prompt prepended to the conversation.
    history:
        Prior conversation turns in OpenAI message format
        (``{"role": "...", "content": "..."}``).
    user_message:
        The latest user message to append.

    Returns
    -------
    list[dict]
        A flat list of ``{"role": ..., "content": ...}`` dicts suitable for
        ``chat.completions.create``.
    """
    messages: list[dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})
    return messages

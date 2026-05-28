"""Runtime configuration for the v2 model-driven agent loop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AgentV2RuntimeConfig:
    max_turns: int
    llm_timeout_seconds: float
    llm_max_tokens: int
    llm_retry_count: int
    openapi_fetch_timeout_seconds: float
    batch_http_get_limit: int
    approval_timeout_seconds: float
    approval_poll_interval_seconds: float
    api_request_timeout_seconds: float
    api_request_retry_count: int


def build_agent_v2_config(settings: Any) -> AgentV2RuntimeConfig:
    """Build v2 config from app settings with bounded defensive defaults."""
    return AgentV2RuntimeConfig(
        max_turns=max(1, int(getattr(settings, "AGENT_V2_MAX_TURNS", 50))),
        llm_timeout_seconds=max(
            1.0, float(getattr(settings, "AGENT_V2_LLM_TIMEOUT_SECONDS", 60.0))
        ),
        llm_max_tokens=max(256, int(getattr(settings, "AGENT_V2_LLM_MAX_TOKENS", 4096))),
        llm_retry_count=max(0, int(getattr(settings, "AGENT_V2_LLM_RETRY_COUNT", 1))),
        openapi_fetch_timeout_seconds=max(
            1.0, float(getattr(settings, "AGENT_V2_OPENAPI_FETCH_TIMEOUT_SECONDS", 30.0))
        ),
        batch_http_get_limit=max(
            1, int(getattr(settings, "AGENT_V2_BATCH_HTTP_GET_LIMIT", 50))
        ),
        approval_timeout_seconds=max(
            1.0, float(getattr(settings, "AGENT_V2_APPROVAL_TIMEOUT_SECONDS", 300.0))
        ),
        approval_poll_interval_seconds=max(
            0.1, float(getattr(settings, "AGENT_V2_APPROVAL_POLL_INTERVAL_SECONDS", 1.0))
        ),
        api_request_timeout_seconds=max(
            1.0, float(getattr(settings, "API_REQUEST_TIMEOUT_SECONDS", 30.0))
        ),
        api_request_retry_count=max(0, int(getattr(settings, "API_REQUEST_RETRY_COUNT", 0))),
    )

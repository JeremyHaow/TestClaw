from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.agent.action_runtime import (
    AGENT_EXECUTION_PROTOCOL_VERSION,
    AgentAction,
    AgentEvaluation,
    AgentEvidence,
    AgentObservation,
    AgentToolCall,
    ValidatedAgentAction,
)


class RuntimePlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    run_id: str | None = None
    stage: str = "agent_runtime"
    actions: list[AgentAction | dict[str, Any]] = Field(default_factory=list)
    budget: dict[str, Any] = Field(default_factory=dict)


class ToolExecutionResult(BaseModel):
    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)

    schema_version: str = AGENT_EXECUTION_PROTOCOL_VERSION
    tool_name: str
    layer: str = "unknown"
    status: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    elapsed_ms: float | None = None
    error: str | None = None
    raw: Any = None


class RuntimeDecision(BaseModel):
    model_config = ConfigDict(extra="ignore")

    next_action: str = "report"
    sufficient_evidence: bool = False
    confidence: str = "medium"
    reason: str = ""
    failure_type: str | None = None
    missing_evidence: list[str] = Field(default_factory=list)


__all__ = [
    "AgentAction",
    "AgentEvaluation",
    "AgentEvidence",
    "AgentObservation",
    "AgentToolCall",
    "RuntimeDecision",
    "RuntimePlan",
    "ToolExecutionResult",
    "ValidatedAgentAction",
]

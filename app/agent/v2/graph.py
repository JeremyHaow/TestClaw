"""Simplified 3-node LangGraph for the v2 agent architecture.

Replaces the old 14-node graph with:
  agent_loop -> report -> END

All logic lives in ``AgentLoop``; the graph is a thin container that
initializes components, runs the loop, and finalizes the report.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, StateGraph

from app.agent.runtime.tool_executor import ToolExecutor
from app.agent.state import AgentState
from app.agent.v2.agent_loop import AgentLoop
from app.agent.v2.approval import ApprovalChannel
from app.agent.v2.config import build_agent_v2_config
from app.agent.v2.llm_bridge import LLMBridge
from app.agent.v2.safety_guard import SafetyGuard

logger = logging.getLogger(__name__)


async def agent_loop_node(state: AgentState) -> AgentState:
    """Core agent loop node -- LLM drives all decisions."""
    from app.config import settings

    config = build_agent_v2_config(settings)
    llm = LLMBridge(
        model=getattr(settings, "DEFAULT_MODEL_PLANNER", "gpt-4o"),
        api_key=getattr(settings, "DEFAULT_OPENAI_API_KEY", ""),
        base_url=getattr(settings, "DEFAULT_OPENAI_BASE_URL", None),
        timeout=config.llm_timeout_seconds,
        max_retries=config.llm_retry_count,
    )

    tool_executor = ToolExecutor(state)
    safety_guard = SafetyGuard(
        execution_policy=state.get("api_execution_policy", "safe_read_only"),
    )

    # Create approval channel and register it so the API can resolve requests.
    approval_channel = ApprovalChannel(state, timeout_seconds=config.approval_timeout_seconds)
    state["_approval_channel"] = approval_channel

    task_id = state.get("task_id")
    if task_id:
        from app.agent.v2.approval import register

        register(task_id, approval_channel)

    agent = AgentLoop(
        llm=llm,
        tool_executor=tool_executor,
        safety_guard=safety_guard,
        state=state,
        approval_channel=approval_channel,
        config=config,
    )

    # Run the agent loop, collecting the final report from events.
    final_report: dict[str, Any] | None = None
    try:
        async for event in agent.run(state.get("objective", "")):
            if event["type"] == "finished":
                final_report = event.get("report")
    finally:
        # Clean up the registry entry when the run ends.
        if task_id:
            from app.agent.v2.approval import remove

            remove(task_id)

    state["final_report"] = final_report or {"verdict": "INCOMPLETE"}
    return state


async def report_node(state: AgentState) -> AgentState:
    """Report node -- finalize and persist the report."""
    report = state.get("final_report") or {"verdict": "NO_REPORT"}

    state.setdefault("workflow_steps", []).append(
        {
            "node": "report",
            "status": "done",
            "detail": f"verdict={report.get('verdict', 'unknown')}",
        }
    )

    return state


def build_v2_graph():
    """Build the simplified 3-node v2 graph."""
    graph = StateGraph(AgentState)

    # Add nodes
    graph.add_node("agent_loop", agent_loop_node)
    graph.add_node("report", report_node)

    # Set entry point
    graph.set_entry_point("agent_loop")

    # Linear flow: agent_loop -> report -> END
    graph.add_edge("agent_loop", "report")
    graph.add_edge("report", END)

    return graph.compile()


# Singleton
v2_agent_graph = build_v2_graph()

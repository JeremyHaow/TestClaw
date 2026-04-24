import logging

from app.agent.state import AgentState
from app.agent.nodes.source_loader import classify_input

logger = logging.getLogger(__name__)


async def run(state: AgentState) -> AgentState:
    source = state.get("source_input", "")
    if not source:
        state["input_type"] = "unknown"
        state.setdefault("workflow_steps", []).append(
            {"node": "input_classifier", "status": "failed", "detail": "No source input provided"}
        )
        return state

    input_type = classify_input(source)
    state["input_type"] = input_type

    logger.info("Input classified as: %s (source=%s)", input_type, source[:80])

    state.setdefault("workflow_steps", []).append(
        {"node": "input_classifier", "status": "done", "detail": f"Detected: {input_type}"}
    )
    return state

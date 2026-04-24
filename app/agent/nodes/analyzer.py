from app.agent.state import AgentState
from app.config import settings


def route(state: AgentState) -> str:
    result = state.get("execution_result") or {}
    retry = state.get("retry_count", 0)
    if result.get("status_code") == 0:
        return "success"
    if retry >= settings.MAX_RETRY_COUNT:
        return "max_retry"
    stderr = result.get("stderr", "")
    if "TimeoutError" in stderr or "locator not found" in stderr or "No generated code" in stderr:
        return "self_heal"
    return "rca"


async def run(state: AgentState) -> AgentState:
    result = state.get("execution_result") or {}
    if result.get("status_code") == 0:
        state["last_error"] = None
        state.setdefault("workflow_steps", []).append(
            {"node": "analyzer", "status": "done", "detail": "All assertions passed"}
        )
        return state
    state["last_error"] = result.get("stderr") or "Unknown execution error"
    state.setdefault("workflow_steps", []).append(
        {"node": "analyzer", "status": "failed", "detail": f"Error: {state['last_error'][:120]}"}
    )
    return state
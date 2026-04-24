from app.agent.state import AgentState
from app.tools.playwright_tool import execute_playwright_test


async def run(state: AgentState) -> AgentState:
    code = state.get("generated_code")
    if not code:
        state["execution_result"] = {"status_code": 1, "stdout": "", "stderr": "No generated code", "trace_path": None}
        state.setdefault("workflow_steps", []).append(
            {"node": "executor", "status": "failed", "detail": "No generated code to execute"}
        )
        return state
    result = execute_playwright_test.invoke({"code_content": code})
    state["execution_result"] = result
    status = "done" if result.get("status_code") == 0 else "failed"
    state.setdefault("workflow_steps", []).append(
        {"node": "executor", "status": status, "detail": f"Exit code: {result.get('status_code')}"}
    )
    return state
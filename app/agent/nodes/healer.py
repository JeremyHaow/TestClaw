import logging

from langchain_core.messages import HumanMessage

from app.agent.prompts import HEALER_PROMPT
from app.agent.state import AgentState
from app.core.llm_gateway import llm_gateway

logger = logging.getLogger(__name__)


async def run(state: AgentState) -> AgentState:
    state["retry_count"] = state.get("retry_count", 0) + 1
    previous_code = state.get("generated_code") or ""
    error_log = state.get("last_error") or ""
    old_locator = state.get("old_locator") or ""
    db = state.get("db_session")
    healed_code = None

    if db and error_log:
        try:
            llm = await llm_gateway.get_coder(db)
            prompt = HEALER_PROMPT.format(
                error_log=error_log[:2000],
                old_locator=old_locator,
                new_dom="DOM structure not available in current context",
            )
            resp = await llm.ainvoke([HumanMessage(content=prompt)])
            content = resp.content if hasattr(resp, "content") else str(resp)
            text = content.strip()
            if text.startswith("```python"):
                text = text[9:].rsplit("```", 1)[0].strip()
            elif text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            if "def test" in text or "import" in text:
                healed_code = text
        except Exception as e:
            logger.warning("Healer LLM call failed: %s, using fallback", e)

    if healed_code is None:
        healed_code = previous_code + "\n# self-heal retry applied\n"

    state["generated_code"] = healed_code
    state.setdefault("workflow_steps", []).append(
        {"node": "healer", "status": "done", "detail": f"Self-heal attempt #{state['retry_count']}"}
    )
    return state

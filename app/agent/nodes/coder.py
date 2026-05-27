import json
import logging

from langchain_core.messages import HumanMessage

from app.agent.prompts import API_CODER_PROMPT, CODER_PROMPT
from app.agent.state import AgentState
from app.core.llm_gateway import ainvoke_with_timeout, llm_gateway

logger = logging.getLogger(__name__)

FALLBACK_SCRIPT = """from playwright.sync_api import Page, expect


def test_generated_flow(page: Page):
    page.goto("{target_url}")
    expect(page).to_have_url("{target_url}")
"""


async def run(state: AgentState) -> AgentState:
    target_url = state.get("target_url") or "http://localhost"
    objective = state.get("objective", "")
    test_type = (state.get("test_type") or "full").lower()
    test_plan = state.get("test_plan") or []
    test_cases = state.get("test_cases") or []
    rag_context = state.get("rag_context") or ""
    db = state.get("db_session")
    code = None

    if db:
        try:
            llm = await llm_gateway.get_coder(db)

            if test_type == "api":
                prompt = API_CODER_PROMPT.format(
                    endpoint_schema=json.dumps(test_plan, ensure_ascii=False, default=str),
                    auth_type="Bearer token",
                    base_url=target_url,
                )
            else:
                plan_desc = json.dumps(test_plan + test_cases, ensure_ascii=False, default=str)
                prompt = CODER_PROMPT.format(
                    clean_dom="DOM tree will be captured during execution",
                    test_plan=f"Objective: {objective}\nPlan: {plan_desc}",
                    rag_context=rag_context or "No additional context",
                )

            resp = await ainvoke_with_timeout(
                llm,
                [HumanMessage(content=prompt)],
                call_name="coder.generate_script",
            )
            content = resp.content if hasattr(resp, "content") else str(resp)
            # Strip markdown fences
            text = content.strip()
            if text.startswith("```python"):
                text = text[9:].rsplit("```", 1)[0].strip()
            elif text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            if "def test" in text or "import" in text:
                code = text
        except Exception as e:
            logger.warning("Coder LLM call failed: %s, using fallback", e)

    if code is None:
        code = FALLBACK_SCRIPT.format(target_url=target_url)

    state["generated_code"] = code
    state.setdefault("workflow_steps", []).append(
        {"node": "coder", "status": "done", "detail": "Generated test script"}
    )
    return state

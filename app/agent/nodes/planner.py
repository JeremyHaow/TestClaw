import json
import logging

from langchain_core.messages import HumanMessage

from app.agent.prompts import PLANNER_PROMPT
from app.agent.state import AgentState
from app.core.llm_gateway import llm_gateway

logger = logging.getLogger(__name__)


async def run(state: AgentState) -> AgentState:
    objective = state.get("objective", "")
    target_url = state.get("target_url", "")
    test_type = state.get("test_type", "auto")
    input_type = state.get("input_type", "unknown")
    parsed_api_schema = state.get("parsed_api_schema")
    db = state.get("db_session")

    # Build API schema summary for the prompt
    schema_summary = "No API schema available"
    if parsed_api_schema:
        schema_summary = json.dumps(
            [
                {
                    "path": ep.get("path"),
                    "method": ep.get("method"),
                    "summary": ep.get("summary"),
                    "auth_required": ep.get("auth_required"),
                    "required_fields": ep.get("required_fields", []),
                }
                for ep in parsed_api_schema[:20]  # limit for prompt size
            ],
            ensure_ascii=False,
            default=str,
        )

    api_plan = None
    ui_plan = None

    if db:
        try:
            llm = await llm_gateway.get_planner(db)
            prompt = PLANNER_PROMPT.format(
                input_type=input_type,
                objective=objective,
                target_url=target_url,
                api_schema_summary=schema_summary,
            )
            resp = await llm.ainvoke([HumanMessage(content=prompt)])
            content = resp.content if hasattr(resp, "content") else str(resp)
            text = content.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                api_plan = parsed.get("api_plan")
                ui_plan = parsed.get("ui_plan")
        except Exception as e:
            logger.warning("Planner LLM call failed: %s, using fallback", e)

    # Fallback plans
    if not api_plan:
        api_plan = {
            "title": "API 测试计划",
            "scope": f"测试 {target_url} 的 API 接口",
            "strategy": "基于 API Schema 自动生成冒烟、参数校验、异常分支测试",
            "categories": ["冒烟测试", "参数校验", "异常分支", "鉴权测试"],
            "estimated_case_count": len(parsed_api_schema) * 3 if parsed_api_schema else 5,
        }

    if not ui_plan:
        ui_plan = {
            "title": "UI 测试计划",
            "scope": f"测试 {target_url} 的页面功能",
            "strategy": "基于页面结构自动生成页面加载、交互、表单、导航测试",
            "categories": ["页面可访问", "关键交互", "表单验证", "跳转", "错误提示"],
            "estimated_case_count": 5,
        }

    state["api_plan"] = api_plan
    state["ui_plan"] = ui_plan

    # Legacy combined plan for backward compat
    state["test_plan"] = [
        {
            "title": api_plan.get("title", "API Test Plan"),
            "target_url": target_url,
            "test_type": "api",
            "phase": "planning",
            "steps": api_plan.get("categories", []),
            "priority": "P1",
        },
        {
            "title": ui_plan.get("title", "UI Test Plan"),
            "target_url": target_url,
            "test_type": "ui",
            "phase": "planning",
            "steps": ui_plan.get("categories", []),
            "priority": "P1",
        },
    ]

    state.setdefault("workflow_steps", []).append(
        {
            "node": "planner",
            "status": "done",
            "detail": f"Generated API plan ({api_plan.get('title')}) + UI plan ({ui_plan.get('title')})",
        }
    )
    return state

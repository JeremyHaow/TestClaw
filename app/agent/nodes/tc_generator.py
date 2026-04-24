import json
import logging

from langchain_core.messages import HumanMessage

from app.agent.prompts import CASE_GENERATOR_PROMPT
from app.agent.state import AgentState
from app.core.llm_gateway import llm_gateway

logger = logging.getLogger(__name__)


async def run(state: AgentState) -> AgentState:
    api_plan = state.get("api_plan")
    ui_plan = state.get("ui_plan")
    parsed_api_schema = state.get("parsed_api_schema")
    input_type = state.get("input_type", "unknown")
    db = state.get("db_session")

    api_cases = []
    ui_cases = []

    if db:
        try:
            llm = await llm_gateway.get_planner(db)

            plan_summary = json.dumps(
                {"api_plan": api_plan, "ui_plan": ui_plan},
                ensure_ascii=False,
                default=str,
            )
            schema_str = json.dumps(parsed_api_schema, ensure_ascii=False, default=str)[:4000] if parsed_api_schema else "No schema"

            prompt = CASE_GENERATOR_PROMPT.format(
                test_plan=plan_summary,
                api_schema=schema_str,
                input_type=input_type,
            )
            resp = await llm.ainvoke([HumanMessage(content=prompt)])
            content = resp.content if hasattr(resp, "content") else str(resp)
            text = content.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            parsed = json.loads(text)

            if isinstance(parsed, dict):
                api_cases = parsed.get("api_cases", parsed.get("api", []))
                ui_cases = parsed.get("ui_cases", parsed.get("ui", []))
            elif isinstance(parsed, list):
                # Flat list — split by case_type
                for item in parsed:
                    case_type = item.get("case_type", "api")
                    if case_type == "ui":
                        ui_cases.append(item)
                    else:
                        api_cases.append(item)
        except Exception as e:
            logger.warning("Case generator LLM call failed: %s, using fallback", e)

    # Fallback: generate basic cases from schema
    if not api_cases and parsed_api_schema:
        for ep in parsed_api_schema[:10]:
            api_cases.append({
                "title": f"SMOKE {ep.get('method', 'GET')} {ep.get('path', '')}",
                "endpoint": ep.get("path", ""),
                "method": ep.get("method", "GET"),
                "preconditions": "API is accessible",
                "steps": [f"Send {ep.get('method', 'GET')} request to {ep.get('path', '')}"],
                "expected": ["Response status is 200"],
                "priority": "P1",
                "category": "SMOKE",
                "case_type": "api",
                "request_template": {
                    "method": ep.get("method", "GET"),
                    "url": ep.get("path", ""),
                    "headers": {},
                    "query_params": {},
                    "body": ep.get("example_request"),
                },
                "assertions": [
                    {"type": "status_code", "expected": int(ep.get("response_status", "200"))},
                ],
            })

    if not ui_cases:
        target_url = state.get("target_url", "")
        ui_cases.append({
            "title": "页面可访问性检查",
            "url": target_url,
            "preconditions": "目标网站可访问",
            "steps": ["打开目标页面", "检查页面加载完成"],
            "expected": ["页面正常加载，无错误"],
            "priority": "P1",
            "category": "PAGE_LOAD",
            "case_type": "ui",
            "playwright_commands": [
                f"open {target_url}",
                "snapshot",
                "screenshot",
            ],
            "assertions": [],
        })

    state["api_cases"] = api_cases
    state["ui_cases"] = ui_cases

    # Legacy combined cases
    state["test_cases"] = api_cases + ui_cases

    # Persist to DB
    if db and (api_cases or ui_cases):
        try:
            from app.models.test_case import TestCase
            task_id = state.get("task_id", "unknown")
            for case in api_cases + ui_cases:
                tc = TestCase(
                    title=case.get("title", "Generated test case"),
                    steps=case.get("steps", []),
                    expected=case.get("expected", []),
                    priority=case.get("priority", "P1"),
                    category=case.get("category", "FUNCTIONAL"),
                    test_data=case.get("request_template") or case.get("playwright_commands") or {},
                    source=f"agent:{task_id}",
                )
                db.add(tc)
            await db.flush()
        except Exception as e:
            logger.warning("Failed to persist test cases: %s", e)

    state.setdefault("workflow_steps", []).append(
        {
            "node": "tc_generator",
            "status": "done",
            "detail": f"Generated {len(api_cases)} API + {len(ui_cases)} UI test case(s)",
        }
    )
    return state

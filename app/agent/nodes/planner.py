import json
import logging

from langchain_core.messages import HumanMessage

from app.agent.analysis.auth_chain import extract_auth_chain, get_auth_test_hints
from app.agent.analysis.scene_detector import detect_scenes, summarize_scenes
from app.agent.analysis.token_budget import apply_schema_budget
from app.agent.progress import persist_progress
from app.agent.prompts import PLANNER_PROMPT
from app.agent.state import AgentState
from app.agent.tool_registry import install_tool_context, record_tool_call
from app.core.llm_gateway import llm_gateway

logger = logging.getLogger(__name__)


async def run(state: AgentState) -> AgentState:
    objective = state.get("objective", "")
    target_url = state.get("target_url", "")
    test_type = state.get("test_type", "auto")
    input_type = state.get("input_type", "unknown")
    parsed_api_schema = state.get("parsed_api_schema")
    db = state.get("db_session")
    install_tool_context(state)

    # --- Pre-analysis: scene detection + auth chain ---
    scene_hints = []
    auth_chain = None
    auth_test_hints = []
    schema_for_prompt = parsed_api_schema

    if parsed_api_schema:
        # Detect scenes
        scene_hints = detect_scenes(parsed_api_schema)
        scene_summary = summarize_scenes(scene_hints)
        logger.info("Detected scenes:\n%s", scene_summary)

        # Extract auth chain
        auth_chain = extract_auth_chain(parsed_api_schema)
        auth_test_hints = get_auth_test_hints(auth_chain)
        logger.info("Auth chain: %s", auth_chain.auth_type)

        # Apply token budget to schema
        schema_for_prompt = apply_schema_budget(parsed_api_schema)

    # Build schema summary for prompt (with budget applied)
    schema_summary = "No API schema available"
    if schema_for_prompt:
        schema_summary = json.dumps(
            [
                {
                    "path": ep.get("path"),
                    "method": ep.get("method"),
                    "summary": ep.get("summary"),
                    "auth_required": ep.get("auth_required"),
                    "required_fields": ep.get("required_fields", []),
                }
                for ep in schema_for_prompt[:30]
            ],
            ensure_ascii=False,
            default=str,
        )

    # Enrich schema summary with scene and auth info
    enrichment = ""
    if scene_hints:
        enrichment += f"\n## Detected API Scenes\n{summarize_scenes(scene_hints)}\n"
    if auth_chain and auth_chain.auth_type != "none":
        enrichment += f"\n## Authentication Chain\n{auth_chain.summary}\n"
        if auth_test_hints:
            enrichment += "\n## Auth Test Hints\n"
            for hint in auth_test_hints:
                enrichment += f"- {hint['title']}: {' -> '.join(hint['steps'])}\n"

    if enrichment:
        schema_summary += enrichment

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

    # --- Build scene-aware fallback plans ---
    scene_names = [h.scene for h in scene_hints]
    has_auth = auth_chain and auth_chain.auth_type != "none"
    has_crud = "crud-resource" in scene_names
    has_upload = "file-upload" in scene_names
    has_search = "search-filter" in scene_names
    has_pagination = "pagination" in scene_names

    if not api_plan:
        categories = ["冒烟测试"]
        if has_crud:
            categories.extend(["CRUD 完整性", "参数校验"])
        if has_auth:
            categories.extend(["鉴权测试", "权限绕过"])
        if has_upload:
            categories.append("文件上传")
        if has_search:
            categories.append("搜索过滤")
        if has_pagination:
            categories.append("分页边界")
        categories.extend(["异常分支", "边界值"])
        # Deduplicate
        seen = set()
        unique = []
        for c in categories:
            if c not in seen:
                seen.add(c)
                unique.append(c)
        categories = unique

        api_plan = {
            "title": "API 测试计划",
            "scope": f"测试 {target_url} 的 API 接口",
            "strategy": f"基于场景检测({', '.join(scene_names[:3]) if scene_names else '通用'})自动生成测试",
            "categories": categories,
            "scene_hints": [h.scene for h in scene_hints],
            "auth_type": auth_chain.auth_type if auth_chain else "unknown",
            "estimated_case_count": len(parsed_api_schema) * 3 if parsed_api_schema else 5,
            "skills": [
                skill["name"]
                for skill in state.get("skill_plan", [])
                if skill.get("layer") == "api"
            ],
        }

    if not ui_plan:
        ui_categories = ["页面可访问", "关键交互"]
        if has_auth:
            ui_categories.extend(["登录流程", "会话管理"])
        if has_crud:
            ui_categories.extend(["列表展示", "表单提交", "删除确认"])
        if has_search:
            ui_categories.extend(["搜索功能", "筛选功能"])
        ui_categories.extend(["错误提示", "响应式布局"])

        ui_plan = {
            "title": "UI 测试计划",
            "scope": f"测试 {target_url} 的页面功能",
            "strategy": "基于页面结构和 API 场景自动生成 UI 测试",
            "categories": ui_categories,
            "estimated_case_count": 5,
            "skills": [
                skill["name"]
                for skill in state.get("skill_plan", [])
                if skill.get("layer") == "ui"
            ],
        }

    if test_type == "ui":
        api_plan = None
    elif test_type == "api":
        ui_plan = None

    if isinstance(api_plan, dict):
        api_plan.setdefault(
            "skills",
            [skill["name"] for skill in state.get("skill_plan", []) if skill.get("layer") == "api"],
        )
    if isinstance(ui_plan, dict):
        ui_plan.setdefault(
            "skills",
            [skill["name"] for skill in state.get("skill_plan", []) if skill.get("layer") == "ui"],
        )

    state["api_plan"] = api_plan
    state["ui_plan"] = ui_plan
    state["scene_hints"] = [{"scene": h.scene, "confidence": h.confidence, "detail": h.detail} for h in scene_hints]
    state["auth_chain"] = {
        "auth_type": auth_chain.auth_type if auth_chain else "unknown",
        "credentials": [
            {"name": c.name, "type": c.cred_type, "source": c.source_endpoint, "consumed_by_count": len(c.consumed_by)}
            for c in (auth_chain.credentials if auth_chain else [])
        ],
    }

    # Legacy combined plan for backward compat
    combined_plan = []
    if api_plan:
        combined_plan.append(
            {
                "title": api_plan.get("title", "API Test Plan"),
                "target_url": target_url,
                "test_type": "api",
                "phase": "planning",
                "steps": api_plan.get("categories", []),
                "priority": "P1",
            }
        )
    if ui_plan:
        combined_plan.append(
            {
                "title": ui_plan.get("title", "UI Test Plan"),
                "target_url": target_url,
                "test_type": "ui",
                "phase": "planning",
                "steps": ui_plan.get("categories", []),
                "priority": "P1",
            }
        )
    state["test_plan"] = combined_plan

    record_tool_call(
        state,
        tool_name="planner.generate_execution_plan",
        layer="planner",
        status="success",
        input_summary={
            "input_type": input_type,
            "test_type": test_type,
            "endpoint_count": len(parsed_api_schema or []),
        },
        output_summary={
            "api_plan": bool(api_plan),
            "ui_plan": bool(ui_plan),
            "selected_skills": [skill.get("name") for skill in state.get("skill_plan", [])],
        },
    )

    scene_str = ", ".join(scene_names[:3]) if scene_names else "none"
    auth_str = auth_chain.auth_type if auth_chain else "unknown"
    plan_titles = " + ".join(
        plan.get("title", "Plan") for plan in (api_plan, ui_plan) if isinstance(plan, dict)
    ) or "No active plan"
    detail = f"Planned: {plan_titles} (scenes={scene_str}, auth={auth_str})"
    state.setdefault("workflow_steps", []).append(
        {"node": "planner", "status": "done", "detail": detail}
    )
    await persist_progress(state, "planner", "done", detail)
    return state

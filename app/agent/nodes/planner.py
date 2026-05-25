import json
import logging

from langchain_core.messages import HumanMessage

from app.agent.analysis.auth_chain import extract_auth_chain, get_auth_test_hints
from app.agent.analysis.scene_detector import detect_scenes, summarize_scenes
from app.agent.analysis.token_budget import apply_schema_budget
from app.agent.json_utils import parse_llm_json_object
from app.agent.progress import persist_progress
from app.agent.prompts import PLANNER_PROMPT, STRATEGY_PLANNER_PROMPT
from app.agent.state import AgentState
from app.agent.strategy import (
    fallback_agent_strategy_decision,
    normalize_agent_strategy_decision,
    strategy_summary,
)
from app.agent.tool_registry import install_tool_context, record_tool_call
from app.core.llm_gateway import llm_gateway

logger = logging.getLogger(__name__)


def _auth_preflight_summary(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    return {
        "status": value.get("status"),
        "strategy": value.get("strategy"),
        "can_start": value.get("can_start"),
        "missing_fields": value.get("missing_fields") or [],
        "protected_validation_count": value.get("protected_validation_count"),
    }


async def run(state: AgentState) -> AgentState:
    objective = state.get("objective", "")
    target_url = state.get("target_url", "")
    test_type = state.get("test_type", "auto")
    input_type = state.get("input_type", "unknown")
    parsed_api_schema = state.get("parsed_api_schema")
    rag_context = state.get("rag_context") or "No relevant prior testing knowledge"
    rag_retrieval = state.get("rag_retrieval") or {}
    mission_plan = state.get("agent_mission_plan") or {}
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
    llm = None

    if db:
        try:
            llm = await llm_gateway.get_planner(db)
            mission_plan_context = json.dumps(
                {
                    "control_pattern": mission_plan.get("control_pattern"),
                    "subgoals": mission_plan.get("subgoals", [])[:8],
                    "memory_needs": mission_plan.get("memory_needs", [])[:5],
                    "environment_needs": mission_plan.get("environment_needs", [])[:6],
                    "success_criteria": mission_plan.get("success_criteria", [])[:5],
                },
                ensure_ascii=False,
                default=str,
            )[:5000] if isinstance(mission_plan, dict) else "{}"
            tool_context = json.dumps(
                {
                    "skills": state.get("skill_plan", []),
                    "roster": state.get("agent_roster", []),
                },
                ensure_ascii=False,
                default=str,
            )[:4000]
            prompt = PLANNER_PROMPT.format(
                input_type=input_type,
                objective=objective,
                target_url=target_url,
                mission_plan=mission_plan_context,
                tool_context=tool_context,
                api_schema_summary=schema_summary,
                rag_context=rag_context,
            )
            resp = await llm.ainvoke([HumanMessage(content=prompt)])
            content = resp.content if hasattr(resp, "content") else str(resp)
            parsed = parse_llm_json_object(str(content))
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
            "mission_subgoals": [
                subgoal.get("id")
                for subgoal in mission_plan.get("subgoals", [])
                if isinstance(subgoal, dict)
            ][:8] if isinstance(mission_plan, dict) else [],
            "estimated_case_count": len(parsed_api_schema) * 3 if parsed_api_schema else 5,
            "skills": [
                skill["name"]
                for skill in state.get("skill_plan", [])
                if skill.get("layer") == "api"
            ],
            "rag_source_count": len(rag_retrieval.get("sources") or []),
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
            "mission_subgoals": [
                subgoal.get("id")
                for subgoal in mission_plan.get("subgoals", [])
                if isinstance(subgoal, dict)
            ][:8] if isinstance(mission_plan, dict) else [],
            "skills": [
                skill["name"]
                for skill in state.get("skill_plan", [])
                if skill.get("layer") == "ui"
            ],
            "rag_source_count": len(rag_retrieval.get("sources") or []),
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
        api_plan.setdefault("rag_source_count", len(rag_retrieval.get("sources") or []))
    if isinstance(ui_plan, dict):
        ui_plan.setdefault(
            "skills",
            [skill["name"] for skill in state.get("skill_plan", []) if skill.get("layer") == "ui"],
        )
        ui_plan.setdefault("rag_source_count", len(rag_retrieval.get("sources") or []))

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

    strategy_error = None
    strategy_decision = None
    if db:
        try:
            if llm is None:
                llm = await llm_gateway.get_planner(db)
            strategy_prompt = STRATEGY_PLANNER_PROMPT.format(
                test_type=test_type,
                input_type=input_type,
                objective=objective,
                target_url=target_url,
                api_execution_policy=state.get("api_execution_policy") or "safe_read_only",
                auth_preflight=json.dumps(
                    _auth_preflight_summary(state.get("auth_preflight")),
                    ensure_ascii=False,
                    default=str,
                ),
                mission_plan=json.dumps(
                    {
                        "control_pattern": mission_plan.get("control_pattern"),
                        "subgoals": mission_plan.get("subgoals", [])[:8],
                        "environment_needs": mission_plan.get("environment_needs", [])[:6],
                        "success_criteria": mission_plan.get("success_criteria", [])[:5],
                    },
                    ensure_ascii=False,
                    default=str,
                )[:5000] if isinstance(mission_plan, dict) else "{}",
                api_schema_summary=schema_summary[:6000],
                rag_context=rag_context[:3000],
                tool_context=json.dumps(
                    {
                        "skills": state.get("skill_plan", []),
                        "roster": state.get("agent_roster", []),
                        "api_plan_available": bool(api_plan),
                        "ui_plan_available": bool(ui_plan),
                    },
                    ensure_ascii=False,
                    default=str,
                )[:4000],
            )
            strategy_resp = await llm.ainvoke([HumanMessage(content=strategy_prompt)])
            strategy_content = (
                strategy_resp.content if hasattr(strategy_resp, "content") else str(strategy_resp)
            )
            strategy_raw = parse_llm_json_object(str(strategy_content))
            if not strategy_raw:
                strategy_error = "Planner strategy response did not contain a JSON object."
            else:
                strategy_decision = normalize_agent_strategy_decision(
                    strategy_raw,
                    parsed_api_schema=parsed_api_schema,
                    execution_policy=str(state.get("api_execution_policy") or "safe_read_only"),
                    test_type=str(test_type),
                    source="llm",
                )
        except Exception as e:
            strategy_error = str(e)
            logger.warning("Strategy planner LLM call failed: %s, using fallback", e)

    if strategy_decision is None:
        strategy_decision = fallback_agent_strategy_decision(
            objective=objective,
            parsed_api_schema=parsed_api_schema,
            execution_policy=str(state.get("api_execution_policy") or "safe_read_only"),
            test_type=str(test_type),
            reason=strategy_error or "Planner model was unavailable.",
        )
    state["agent_strategy_decision"] = strategy_decision
    state["agent_tool_plan"] = strategy_decision.get("tool_plan", [])
    state["agent_strategy_diagnostics"] = strategy_decision.get("diagnostics", [])

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
            "agent_strategy": strategy_summary(strategy_decision),
            "selected_skills": [skill.get("name") for skill in state.get("skill_plan", [])],
            "rag_source_count": len(rag_retrieval.get("sources") or []),
            "mission_subgoals": len(mission_plan.get("subgoals", []))
            if isinstance(mission_plan, dict)
            else 0,
        },
        metadata={
            "reason": "Turn mission subgoals, retrieved memory, tools, and environment observations into an executable test plan.",
            "next_decision": "generate_cases_from_plan",
        },
    )

    record_tool_call(
        state,
        tool_name="planner.select_agent_strategy",
        layer="planner",
        status="success" if strategy_decision.get("valid", True) else "skipped",
        input_summary={
            "test_type": test_type,
            "input_type": input_type,
            "endpoint_count": len(parsed_api_schema or []),
            "api_execution_policy": state.get("api_execution_policy") or "safe_read_only",
        },
        output_summary=strategy_summary(strategy_decision),
        metadata={
            "reason": "Select a model-driven tool strategy and locally validate it against schema and safety policy.",
            "next_decision": "generate_cases_from_validated_strategy",
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

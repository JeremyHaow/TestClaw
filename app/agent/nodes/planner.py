import json
import logging
import re
from typing import Any
from urllib.parse import urlsplit

from langchain_core.messages import HumanMessage

from app.agent.analysis.auth_chain import extract_auth_chain, get_auth_test_hints
from app.agent.analysis.scene_detector import detect_scenes, summarize_scenes
from app.agent.analysis.token_budget import apply_schema_budget
from app.agent.api_scope import safe_schema_method_endpoints
from app.agent.action_runtime import validate_and_record_agent_action_plan
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
from app.core.redaction import redact_sensitive_data, redact_sensitive_text
from app.core.llm_gateway import ainvoke_with_timeout, llm_gateway

logger = logging.getLogger(__name__)


def _schema_endpoint_summary(endpoint: dict) -> dict:
    return {
        "path": endpoint.get("path"),
        "method": endpoint.get("method"),
        "summary": endpoint.get("summary"),
        "auth_required": endpoint.get("auth_required"),
        "required_fields": endpoint.get("required_fields", []),
    }


def _api_schema_prompt_summary(
    parsed_api_schema: list[dict] | None,
    schema_for_prompt: list[dict] | None,
) -> str:
    if not parsed_api_schema:
        return "No API schema available"

    safe_endpoints = safe_schema_method_endpoints(parsed_api_schema)
    write_endpoints = [
        endpoint
        for endpoint in parsed_api_schema
        if str(endpoint.get("method") or "GET").upper() in {"POST", "PUT", "PATCH", "DELETE"}
    ]
    budgeted_examples = [
        _schema_endpoint_summary(endpoint)
        for endpoint in (schema_for_prompt or [])[:30]
        if isinstance(endpoint, dict)
    ]
    payload = {
        "endpoint_count": len(parsed_api_schema),
        "safe_method_endpoint_count": len(safe_endpoints),
        "write_method_endpoint_count": len(write_endpoints),
        "safe_methods": ["GET", "HEAD", "OPTIONS"],
        "safe_endpoint_examples": [
            _schema_endpoint_summary(endpoint) for endpoint in safe_endpoints[:80]
        ],
        "budgeted_endpoint_examples": budgeted_examples,
    }
    return json.dumps(payload, ensure_ascii=False, default=str)


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


def _safe_text(value: Any, limit: int = 500) -> str:
    text = redact_sensitive_text(str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _host(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
    except Exception:
        return ""
    return parsed.netloc.lower()


def _memory_fact_target_related(fact: dict[str, Any], *, target_url: str, source_input: str) -> bool:
    target_hint = str(fact.get("target_hint") or "")
    current_target = target_url or source_input
    fact_host = _host(target_hint)
    current_host = _host(current_target)
    if fact_host and current_host:
        return fact_host == current_host
    if target_hint and current_target:
        return target_hint in current_target or current_target in target_hint
    return False


def _planner_memory_facts(
    state: AgentState,
    rag_retrieval: dict[str, Any],
    *,
    target_url: str,
    source_input: str,
) -> list[dict[str, Any]]:
    raw_facts = state.get("rag_facts") or rag_retrieval.get("facts") or []
    facts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_facts:
        if not isinstance(item, dict):
            continue
        if str(item.get("confidence") or "").lower() != "high":
            continue
        if not _memory_fact_target_related(item, target_url=target_url, source_input=source_input):
            continue
        fact = redact_sensitive_data(
            {
                "fact_type": item.get("fact_type") or "execution_memory",
                "confidence": "high",
                "source_id": item.get("source_id"),
                "source_script_id": item.get("source_script_id"),
                "target_hint": item.get("target_hint"),
                "stage": item.get("stage"),
                "failure_type": item.get("failure_type"),
                "next_action": item.get("next_action"),
                "summary": _safe_text(item.get("summary"), 300),
                "planner_hint": _safe_text(item.get("planner_hint"), 300),
            }
        )
        marker = json.dumps(fact, ensure_ascii=False, sort_keys=True, default=str)
        if marker in seen:
            continue
        seen.add(marker)
        facts.append(fact)
    return facts[:4]


def _memory_fact_lines(memory_facts: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for index, fact in enumerate(memory_facts, start=1):
        failure = f" failure_type={fact.get('failure_type')}" if fact.get("failure_type") else ""
        detail = fact.get("planner_hint") or fact.get("summary") or ""
        lines.append(
            f"{index}. type={fact.get('fact_type')}{failure}; action={fact.get('next_action')}; {_safe_text(detail, 240)}"
        )
    return lines


def _planner_rag_context(rag_context: str, memory_facts: list[dict[str, Any]]) -> str:
    if not memory_facts:
        return rag_context
    lines = [
        "High-confidence target-related memory facts:",
        *_memory_fact_lines(memory_facts),
        "",
        "Retrieved snippets:",
        rag_context,
    ]
    return "\n".join(lines)[:3000]


def _memory_plan_fields(memory_facts: list[dict[str, Any]]) -> dict[str, Any]:
    if not memory_facts:
        return {"memory_fact_count": 0}
    blockers = [
        fact
        for fact in memory_facts
        if fact.get("fact_type") in {"known_blocker", "failure_recovery"}
    ]
    successes = [
        fact
        for fact in memory_facts
        if fact.get("fact_type") == "successful_strategy"
    ]
    fields: dict[str, Any] = {
        "memory_fact_count": len(memory_facts),
        "memory_facts": memory_facts,
    }
    if blockers:
        fields["known_blockers"] = blockers
    if successes:
        fields["historical_success_strategies"] = successes
    return fields


def _attach_memory_fields(plan: dict | None, memory_facts: list[dict[str, Any]]) -> None:
    if not isinstance(plan, dict):
        return
    fields = _memory_plan_fields(memory_facts)
    plan["memory_fact_count"] = fields["memory_fact_count"]
    if memory_facts:
        plan.setdefault("memory_facts", fields["memory_facts"])
        if fields.get("known_blockers"):
            plan.setdefault("known_blockers", fields["known_blockers"])
        if fields.get("historical_success_strategies"):
            plan.setdefault("historical_success_strategies", fields["historical_success_strategies"])


async def run(state: AgentState) -> AgentState:
    objective = state.get("objective", "")
    target_url = state.get("target_url", "")
    test_type = state.get("test_type", "auto")
    input_type = state.get("input_type", "unknown")
    parsed_api_schema = state.get("parsed_api_schema")
    rag_context = state.get("rag_context") or "No relevant prior testing knowledge"
    rag_retrieval = state.get("rag_retrieval") or {}
    memory_facts = _planner_memory_facts(
        state,
        rag_retrieval,
        target_url=target_url,
        source_input=str(state.get("source_input") or ""),
    )
    state["rag_facts"] = memory_facts
    planner_rag_context = _planner_rag_context(rag_context, memory_facts)
    mission_plan = state.get("agent_mission_plan") or {}
    db = state.get("db_session")
    install_tool_context(state)
    await persist_progress(
        state,
        "planner",
        "running",
        "Analyzing mission context and selecting a test strategy",
    )

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

    # Build schema summary for prompt (with budget applied plus explicit safe endpoint context)
    schema_summary = _api_schema_prompt_summary(parsed_api_schema, schema_for_prompt)

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
                    "memory_facts": memory_facts,
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
                rag_context=planner_rag_context,
            )
            resp = await ainvoke_with_timeout(
                llm,
                [HumanMessage(content=prompt)],
                call_name="planner.plan",
            )
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
            "memory_fact_count": len(memory_facts),
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
            "memory_fact_count": len(memory_facts),
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
        _attach_memory_fields(api_plan, memory_facts)
    if isinstance(ui_plan, dict):
        ui_plan.setdefault(
            "skills",
            [skill["name"] for skill in state.get("skill_plan", []) if skill.get("layer") == "ui"],
        )
        ui_plan.setdefault("rag_source_count", len(rag_retrieval.get("sources") or []))
        _attach_memory_fields(ui_plan, memory_facts)

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
                        "memory_facts": memory_facts,
                    },
                    ensure_ascii=False,
                    default=str,
                )[:5000] if isinstance(mission_plan, dict) else "{}",
                api_schema_summary=schema_summary[:6000],
                rag_context=planner_rag_context[:3000],
                tool_context=json.dumps(
                    {
                        "skills": state.get("skill_plan", []),
                        "roster": state.get("agent_roster", []),
                        "api_plan_available": bool(api_plan),
                        "ui_plan_available": bool(ui_plan),
                        "memory_fact_count": len(memory_facts),
                    },
                    ensure_ascii=False,
                    default=str,
                )[:4000],
            )
            strategy_resp = await ainvoke_with_timeout(
                llm,
                [HumanMessage(content=strategy_prompt)],
                call_name="planner.strategy",
            )
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
    validate_and_record_agent_action_plan(
        state,
        stage="planner",
        strategy=strategy_decision,
        parsed_api_schema=parsed_api_schema,
        execution_policy=str(state.get("api_execution_policy") or "safe_read_only"),
    )

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
            "memory_fact_count": len(memory_facts),
            "memory_blocker_count": len(
                [
                    fact
                    for fact in memory_facts
                    if fact.get("fact_type") in {"known_blocker", "failure_recovery"}
                ]
            ),
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

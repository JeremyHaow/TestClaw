import json
import logging

from langchain_core.messages import HumanMessage

from app.agent.analysis.token_budget import apply_schema_budget
from app.agent.progress import persist_progress
from app.agent.prompts import CASE_GENERATOR_PROMPT
from app.agent.state import AgentState
from app.core.llm_gateway import llm_gateway

logger = logging.getLogger(__name__)


def _build_fallback_api_cases(
    endpoints: list[dict],
    scene_hints: list[dict],
    auth_info: dict | None,
) -> list[dict]:
    """Build scene-aware fallback API test cases from schema."""
    cases = []
    has_auth = auth_info and auth_info.get("auth_type") not in ("none", "unknown")

    for ep in endpoints[:15]:
        method = ep.get("method", "GET").upper()
        path = ep.get("path", "")
        required_fields = ep.get("required_fields") or []

        # Extract query params
        query_params = {}
        for qp in ep.get("query_params") or []:
            if isinstance(qp, dict) and qp.get("name"):
                val = qp.get("example")
                if val is None:
                    enum_vals = qp.get("enum") or qp.get("schema", {}).get("enum")
                    if enum_vals:
                        val = enum_vals[0]
                    else:
                        qtype = qp.get("type") or qp.get("schema", {}).get("type", "string")
                        val = (
                            ["test"] if qtype == "array" else
                            1 if qtype == "integer" else
                            "test"
                        )
                query_params[qp["name"]] = val

        # Skip multipart upload endpoints for normal tests
        ct = (ep.get("request_body_content_type") or "").lower()
        is_upload = "multipart" in ct or "form-data" in ct

        # Smoke test for every endpoint
        cases.append({
            "title": f"SMOKE {method} {path}",
            "endpoint": path,
            "method": method,
            "preconditions": "API is accessible",
            "steps": [f"Send {method} request to {path}"],
            "expected": ["Response status is 2xx"],
            "priority": "P1",
            "category": "SMOKE",
            "case_type": "api",
            "request_template": {
                "method": method,
                "url": path,
                "headers": {},
                "query_params": query_params,
                "body": None if is_upload else ep.get("example_request"),
            },
            "assertions": [
                {"type": "status_code", "expected": 200},
            ],
        })

        # Missing required fields test for POST/PUT/PATCH
        if method in ("POST", "PUT", "PATCH") and required_fields:
            for field in required_fields[:2]:
                cases.append({
                    "title": f"MISSING_FIELD {method} {path} (缺少 {field})",
                    "endpoint": path,
                    "method": method,
                    "steps": [f"Send {method} without required field '{field}'"],
                    "expected": ["Response status is 400 or 422"],
                    "priority": "P2",
                    "category": "PARAM_VALIDATION",
                    "case_type": "api",
                    "request_template": {
                        "method": method,
                        "url": path,
                        "headers": {},
                        "body": {f: f"test_{f}" for f in required_fields if f != field},
                    },
                    "assertions": [
                        {"type": "status_code", "expected": [400, 422]},
                    ],
                })

    # Auth-specific cases
    if has_auth:
        # Find first authenticated endpoint
        auth_eps = [
            ep for ep in endpoints
            if ep.get("auth_required") or any(
                (h.get("name", "") if isinstance(h, dict) else str(h)).lower() == "authorization"
                for h in (ep.get("header_params") or [])
            )
        ]
        if auth_eps:
            ep = auth_eps[0]
            cases.append({
                "title": f"UNAUTHORIZED {ep.get('method', 'GET')} {ep.get('path', '')}",
                "endpoint": ep.get("path", ""),
                "method": ep.get("method", "GET").upper(),
                "steps": ["Send request without auth token"],
                "expected": ["Response status is 401 or 403"],
                "priority": "P1",
                "category": "SECURITY",
                "case_type": "api",
                "request_template": {
                    "method": ep.get("method", "GET").upper(),
                    "url": ep.get("path", ""),
                    "headers": {},
                },
                "assertions": [
                    {"type": "status_code", "expected": [401, 403]},
                ],
            })

    return cases


def _build_base_url_api_case(base_url: str) -> list[dict]:
    """Build a minimal API reachability case when only a base URL is available."""
    if not base_url:
        return []
    return [
        {
            "title": f"SMOKE GET {base_url.rstrip('/') or base_url}",
            "endpoint": "/",
            "method": "GET",
            "preconditions": "API base URL is accessible",
            "steps": ["Send GET request to the configured API base URL"],
            "expected": ["Response status is 2xx"],
            "priority": "P1",
            "category": "SMOKE",
            "case_type": "api",
            "request_template": {
                "method": "GET",
                "url": base_url.rstrip("/") or base_url,
                "headers": {},
                "query_params": {},
                "body": None,
            },
            "assertions": [
                {"type": "status_code", "expected": 200},
            ],
        }
    ]


def _build_fallback_ui_cases(
    target_url: str,
    scene_hints: list[dict],
    auth_info: dict | None,
) -> list[dict]:
    """Build comprehensive fallback UI test cases without LLM."""
    scene_names = {h.get("scene", "") for h in scene_hints}
    has_auth = auth_info and auth_info.get("auth_type") not in ("none", "unknown")
    has_crud = "crud-resource" in scene_names
    has_search = "search-filter" in scene_names

    cases = []

    # 1. Page load & basic rendering
    cases.append({
        "title": "页面可访问性与基础渲染检查",
        "url": target_url,
        "steps": ["打开目标页面", "等待页面加载完成", "检查页面标题和关键元素"],
        "expected": ["页面正常加载", "无 JS 错误", "关键元素可见"],
        "priority": "P1",
        "category": "PAGE_LOAD",
        "case_type": "ui",
        "playwright_commands": [f"open {target_url}", "snapshot", "screenshot"],
        "assertions": [],
    })

    # 2. Navigation test
    cases.append({
        "title": "页面导航链接检查",
        "url": target_url,
        "steps": ["打开页面", "检查导航菜单", "点击各导航链接验证跳转"],
        "expected": ["导航链接可点击", "页面正确跳转"],
        "priority": "P1",
        "category": "NAVIGATION",
        "case_type": "ui",
        "playwright_commands": [f"open {target_url}", "snapshot", "screenshot"],
        "assertions": [],
    })

    # 3. Console error check
    cases.append({
        "title": "控制台错误检查",
        "url": target_url,
        "steps": ["打开页面", "检查浏览器控制台是否有错误"],
        "expected": ["无 JavaScript 错误", "无资源加载失败"],
        "priority": "P2",
        "category": "ERROR_DISPLAY",
        "case_type": "ui",
        "playwright_commands": [f"open {target_url}", "snapshot", "screenshot"],
        "assertions": [],
    })

    # 4. Form interaction (if CRUD or auth detected)
    if has_crud or has_auth:
        cases.append({
            "title": "表单交互功能检查",
            "url": target_url,
            "steps": ["打开页面", "查找表单元素", "填写表单字段", "提交表单"],
            "expected": ["表单可交互", "提交后有反馈"],
            "priority": "P1",
            "category": "FORM",
            "case_type": "ui",
            "playwright_commands": [f"open {target_url}", "snapshot", "screenshot"],
            "assertions": [],
        })

    # 5. Auth-specific tests
    if has_auth:
        cases.extend([
            {
                "title": "登录流程功能检查",
                "url": target_url,
                "steps": ["打开登录页面", "检查登录表单", "输入凭据并提交", "验证登录结果"],
                "expected": ["登录表单可交互", "提交后跳转或显示结果"],
                "priority": "P1",
                "category": "AUTH",
                "case_type": "ui",
                "playwright_commands": [f"open {target_url}", "snapshot", "screenshot"],
                "assertions": [],
            },
            {
                "title": "未登录状态访问受保护页面",
                "url": target_url,
                "steps": ["清除登录状态", "尝试访问受保护页面", "检查是否重定向到登录页"],
                "expected": ["重定向到登录页或显示 401"],
                "priority": "P1",
                "category": "AUTH",
                "case_type": "ui",
                "playwright_commands": [f"open {target_url}", "snapshot", "screenshot"],
                "assertions": [],
            },
        ])

    # 6. Search functionality (if detected)
    if has_search:
        cases.append({
            "title": "搜索功能检查",
            "url": target_url,
            "steps": ["打开页面", "找到搜索框", "输入关键词", "执行搜索"],
            "expected": ["搜索框可交互", "搜索结果正确显示"],
            "priority": "P1",
            "category": "INTERACTION",
            "case_type": "ui",
            "playwright_commands": [f"open {target_url}", "snapshot", "screenshot"],
            "assertions": [],
        })

    # 7. Responsive layout check
    cases.append({
        "title": "响应式布局检查",
        "url": target_url,
        "steps": ["打开页面", "调整浏览器窗口大小", "检查布局是否正常"],
        "expected": ["布局在不同尺寸下正常显示", "无元素溢出或重叠"],
        "priority": "P2",
        "category": "PAGE_LOAD",
        "case_type": "ui",
        "playwright_commands": [f"open {target_url}", "snapshot", "screenshot"],
        "assertions": [],
    })

    return cases


async def run(state: AgentState) -> AgentState:
    api_plan = state.get("api_plan")
    ui_plan = state.get("ui_plan")
    parsed_api_schema = state.get("parsed_api_schema")
    input_type = state.get("input_type", "unknown")
    test_type = (state.get("test_type") or "auto").lower()
    scene_hints = state.get("scene_hints") or []
    auth_info = state.get("auth_chain")
    base_url = (state.get("base_url_override") or state.get("target_url") or "").rstrip("/")
    db = state.get("db_session")

    api_cases = list(state.get("api_cases") or [])
    ui_cases = list(state.get("ui_cases") or [])

    if db and not (api_cases or ui_cases):
        try:
            llm = await llm_gateway.get_planner(db)

            plan_summary = json.dumps(
                {"api_plan": api_plan, "ui_plan": ui_plan},
                ensure_ascii=False,
                default=str,
            )

            # Apply token budget to schema for prompt
            budgeted_schema = apply_schema_budget(parsed_api_schema) if parsed_api_schema else None
            schema_str = json.dumps(budgeted_schema, ensure_ascii=False, default=str)[:6000] if budgeted_schema else "No schema"

            # Enrich with scene and auth context
            context = ""
            if scene_hints:
                context += f"\nDetected scenes: {', '.join(h.get('scene', '') for h in scene_hints)}"
            if auth_info and auth_info.get("auth_type") not in ("none", "unknown"):
                context += f"\nAuth type: {auth_info.get('auth_type')}"
                creds = auth_info.get("credentials", [])
                if creds:
                    context += f"\nCredentials: {', '.join(c.get('name', '') for c in creds)}"
            if context:
                schema_str += f"\n\n## Context{context}"

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
                for item in parsed:
                    case_type = item.get("case_type", "api")
                    if case_type == "ui":
                        ui_cases.append(item)
                    else:
                        api_cases.append(item)
        except Exception as e:
            logger.warning("Case generator LLM call failed: %s, using fallback", e)

    # Scene-aware fallback cases
    if test_type != "ui":
        if not api_cases and parsed_api_schema:
            api_cases = _build_fallback_api_cases(parsed_api_schema, scene_hints, auth_info)
        elif not api_cases and (state.get("base_url_override") or test_type in {"api", "full"}):
            api_cases = _build_base_url_api_case(base_url)

    # Only generate UI fallback cases for URL input (not Swagger)
    if test_type != "api" and not ui_cases and input_type == "url":
        target_url = state.get("ui_seed_url") or state.get("target_url", "")
        ui_cases = _build_fallback_ui_cases(target_url, scene_hints, auth_info)

    if test_type == "ui":
        api_cases = []
    elif test_type == "api":
        ui_cases = []

    state["api_cases"] = api_cases
    state["ui_cases"] = ui_cases
    state["test_cases"] = api_cases + ui_cases

    # Persist to DB
    if db and (api_cases or ui_cases):
        try:
            from app.models.test_case import TestCase
            task_id = state.get("task_id", "unknown")
            for case in api_cases + ui_cases:
                steps = case.get("steps", [])
                if isinstance(steps, str):
                    steps = [s.strip() for s in steps.split("\n") if s.strip()]
                expected = case.get("expected", [])
                if isinstance(expected, str):
                    expected = [s.strip() for s in expected.split("\n") if s.strip()]
                tc = TestCase(
                    title=case.get("title", "Generated test case"),
                    steps=steps,
                    expected=expected,
                    priority=case.get("priority", "P1"),
                    category=case.get("category", "FUNCTIONAL"),
                    test_data=case.get("request_template") or case.get("playwright_commands") or {},
                    source=f"agent:{task_id}",
                )
                db.add(tc)
            await db.flush()
        except Exception as e:
            logger.warning("Failed to persist test cases: %s", e)

    detail = f"Generated {len(api_cases)} API + {len(ui_cases)} UI test case(s)"
    state.setdefault("workflow_steps", []).append(
        {"node": "tc_generator", "status": "done", "detail": detail}
    )
    await persist_progress(state, "tc_generator", "done", detail)
    return state

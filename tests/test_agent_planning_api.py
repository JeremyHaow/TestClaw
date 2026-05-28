import asyncio
import json
import uuid
from datetime import datetime
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.v1 import agent_plans
from app.config import settings
from app.core.security import hash_password
from app.database import AsyncSessionLocal
from app.main import app
from app.models.agent_planning import AgentPlan, AgentPlanningMessage
from app.models.task import Task, TaskStatus, TestType as TaskTestType
from app.models.user import User
from app.services.agent_planning import (
    PlannerLLMOutput,
    agent_planning_service,
    normalize_planner_run_payload,
)
from app.tools.doc_parser import parse_api_document_content


def _token(client: TestClient) -> str:
    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "testclaw123"})
    assert login.status_code == 200
    return login.json()["access_token"]


def _login_token(client: TestClient, username: str, password: str) -> str:
    login = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200
    return login.json()["access_token"]


async def _create_test_user(username: str, password: str) -> None:
    async with AsyncSessionLocal() as session:
        session.add(
            User(
                username=username,
                hashed_password=hash_password(password),
                is_active=True,
                is_admin=False,
            )
        )
        await session.commit()


async def _count_structured_agent_plans(session_id: str) -> int:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AgentPlan).where(AgentPlan.session_id == session_id)
        )
        return len(list(result.scalars()))


def _message(content: str) -> AgentPlanningMessage:
    return AgentPlanningMessage(session_id="test-session", role="user", content=content)


def _sse_events(text: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in text.replace("\r\n", "\n").strip().split("\n\n"):
        if not block.strip():
            continue
        event_name = "message"
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].strip())
        if data_lines:
            events.append((event_name, json.loads("\n".join(data_lines))))
    return events


def _assert_no_placeholder_option_messages(question_options: list[dict[str, Any]]) -> None:
    banned = [
        "稍后补充具体地址",
        "我会补充关于",
        "我会直接粘贴目标 URL",
    ]
    for group in question_options:
        for option in group["options"]:
            message = option["message"]
            assert all(text not in message for text in banned)


def test_chinese_message_extraction_for_ui_credentials_and_write_policy() -> None:
    payload = normalize_planner_run_payload(
        None,
        [
            _message(
                "请测试管理后台页面 https://app.example.test/admin "
                "用户名: alice 密码: s3cr3t 固定验证码: 1234 "
                "这是测试环境可以创建修改删除。"
            )
        ],
    )

    assert payload.source == "https://app.example.test/admin"
    assert payload.test_type == "ui"
    assert payload.auth_mode == "auto"
    assert payload.captcha_mode == "static"
    assert payload.api_execution_policy == "write_allowed"
    assert payload.auth_credentials is not None
    assert payload.auth_credentials.username == "alice"
    assert payload.auth_credentials.password == "s3cr3t"
    assert payload.auth_credentials.captcha == "1234"


def test_chinese_message_extraction_for_api_no_auth_dynamic_and_safe_policies() -> None:
    dynamic_payload = normalize_planner_run_payload(
        None,
        [
            _message(
                "请测试接口/API https://api.example.test/openapi.json，"
                "无需鉴权，动态验证码/图片验证码，带鉴权只读。"
            )
        ],
    )
    safe_payload = normalize_planner_run_payload(
        None,
        [
            _message(
                "请测试接口 https://api.example.test/openapi.json，只读，"
                "不要修改，不要删除，不能保存。"
            )
        ],
    )

    assert dynamic_payload.source == "https://api.example.test/openapi.json"
    assert dynamic_payload.test_type == "api"
    assert dynamic_payload.auth_mode == "none_confirmed"
    assert dynamic_payload.captcha_mode == "dynamic"
    assert dynamic_payload.api_execution_policy == "safe_with_auth"
    assert safe_payload.api_execution_policy == "safe_read_only"


def test_multi_direct_api_urls_normalize_to_schema_source() -> None:
    payload = normalize_planner_run_payload(
        None,
        [
            _message(
                "请测试 https://httpbin.org/get 和 https://httpbin.org/headers "
                "两个只读接口，状态码必须是 200，响应 JSON 需要包含 url、headers 字段，并说明证据。"
            )
        ],
    )

    assert payload.test_type == "api"
    assert payload.base_url == "https://httpbin.org"
    assert payload.source is not None
    document = json.loads(payload.source)
    assert document["openapi"] == "3.0.0"
    assert document["servers"] == [{"url": "https://httpbin.org"}]
    assert set(document["paths"]) == {"/get", "/headers"}
    response_schema = document["paths"]["/get"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert response_schema["required"] == ["url", "headers"]
    assert response_schema["x-testclaw-user-required-fields"] is True

    endpoints = parse_api_document_content(payload.source)
    assert [(item["method"], item["path"]) for item in endpoints] == [
        ("GET", "/get"),
        ("GET", "/headers"),
    ]
    assert endpoints[0]["response_schema"]["required"] == ["url", "headers"]


def test_document_asset_handoff_openapi_fence_normalizes_to_executable_source() -> None:
    raw_openapi = {
        "openapi": "3.0.3",
        "info": {"title": "Asset doc", "version": "1.0.0"},
        "servers": [{"url": "https://api.example.test"}],
        "paths": {
            "/health": {"get": {"summary": "Health", "responses": {"200": {"description": "ok"}}}},
        },
    }
    payload = normalize_planner_run_payload(
        None,
        [
            _message(
                "从 TestClaw 接口文档资产创建新测试计划。\n"
                "文档：Asset doc\n"
                "已保存 OpenAPI 原文（已脱敏，仅用于本次计划解析）：\n"
                "```json\n"
                f"{json.dumps(raw_openapi)}\n"
                "```"
            )
        ],
    )

    assert payload.test_type == "api"
    assert payload.base_url == "https://api.example.test"
    assert payload.source is not None
    assert json.loads(payload.source)["paths"] == raw_openapi["paths"]


def test_document_asset_handoff_auth_warning_does_not_fake_manual_token(
    monkeypatch,
) -> None:
    async def fake_llm(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("planner unavailable")

    monkeypatch.setattr(agent_planning_service, "_call_planner_llm", fake_llm)
    raw_openapi = {
        "openapi": "3.0.3",
        "info": {"title": "Asset auth doc", "version": "1.0.0"},
        "servers": [{"url": "https://api.example.test"}],
        "paths": {
            "/profile": {
                "get": {
                    "summary": "Profile requires auth",
                    "security": [{"bearerAuth": []}],
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
        "components": {
            "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}}
        },
    }
    content = (
        "从 TestClaw 接口文档资产创建新测试计划。\n"
        "文档：Asset auth doc\n"
        "需要鉴权端点：1\n"
        "安全边界：默认只读；不要复用凭证、Token、Cookie、会话或验证码值。\n"
        "已保存 OpenAPI 原文（已脱敏，仅用于本次计划解析）：\n"
        "```json\n"
        f"{json.dumps(raw_openapi)}\n"
        "```"
    )

    payload = normalize_planner_run_payload(None, [_message(content)])

    assert payload.token is None
    assert payload.auth_mode == "auto"

    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post("/api/v1/agent-plans", json={}, headers=headers).json()
        response = client.post(
            f"/api/v1/agent-plans/{created['id']}/messages",
            json={"content": content},
            headers=headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "collecting"
    assert body["ready_to_execute"] is False
    assert body["current_run_payload"] is None
    assert any("是否需要鉴权" in message["content"] for message in body["messages"])
    assert body["question_options"][0]["step"] == "auth_boundary"


def test_conversational_question_option_intake_preserves_existing_document_source(
    monkeypatch,
) -> None:
    async def fake_llm(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("planner unavailable")

    monkeypatch.setattr(agent_planning_service, "_call_planner_llm", fake_llm)
    raw_openapi = {
        "openapi": "3.0.3",
        "info": {"title": "Asset auth doc", "version": "1.0.0"},
        "servers": [{"url": "https://api.example.test"}],
        "paths": {
            "/profile": {
                "get": {
                    "summary": "Profile requires auth",
                    "security": [{"bearerAuth": []}],
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
        "components": {
            "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}}
        },
    }
    content = (
        "从 TestClaw 接口文档资产创建新测试计划。\n"
        "文档：Asset auth doc\n"
        "需要鉴权端点：1\n"
        "安全边界：默认只读；不要复用凭证、Token、Cookie、会话或验证码值。\n"
        "已保存 OpenAPI 原文（已脱敏，仅用于本次计划解析）：\n"
        "```json\n"
        f"{json.dumps(raw_openapi)}\n"
        "```"
    )

    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post("/api/v1/agent-plans", json={}, headers=headers).json()
        first = client.post(
            f"/api/v1/agent-plans/{created['id']}/messages",
            json={"content": content},
            headers=headers,
        )
        response = client.post(
            f"/api/v1/agent-plans/sessions/{created['id']}/intake",
            json={
                "action": "continue",
                "current_step": "auth_boundary",
                "selected_option": {
                    "label": "手动鉴权",
                    "value": "manual_auth",
                    "field": "auth_boundary",
                    "step": "auth_boundary",
                    "message": "登录方式/凭证：使用手动提供的 Token、Cookie 或 Header。",
                },
                "message": "Authorization: Bearer valid-token。成功标准：只读 GET 接口有证据。",
            },
            headers=headers,
        )

    assert first.status_code == 200
    assert first.json()["status"] == "collecting"
    assert response.status_code == 200
    body = response.json()
    assert body["session"]["status"] == "ready"
    assert body["session"]["current_step"] == "review"
    assert body["session"]["current_run_payload"]["source"]
    assert json.loads(body["session"]["current_run_payload"]["source"])["paths"] == raw_openapi["paths"]
    assert body["session"]["current_run_payload"]["token"] == "[REDACTED]"
    assert body["next_question"] is None


def test_multi_direct_api_urls_preserve_query_examples() -> None:
    payload = normalize_planner_run_payload(
        None,
        [
            _message(
                "Test API endpoints https://api.example.test/search?q=codex&page=1 "
                "and https://api.example.test/users?active=true. Response includes data field."
            )
        ],
    )

    assert payload.source is not None
    document = json.loads(payload.source)
    assert document["servers"] == [{"url": "https://api.example.test"}]
    search_params = document["paths"]["/search"]["get"]["parameters"]
    assert search_params == [
        {
            "name": "q",
            "in": "query",
            "required": False,
            "schema": {"type": "string"},
            "example": "codex",
        },
        {
            "name": "page",
            "in": "query",
            "required": False,
            "schema": {"type": "string"},
            "example": "1",
        },
    ]
    endpoints = parse_api_document_content(payload.source)
    search = next(item for item in endpoints if item["path"] == "/search")
    assert {item["name"]: item["example"] for item in search["query_params"]} == {
        "q": "codex",
        "page": "1",
    }


def test_api_plan_without_auth_boundary_keeps_collecting(monkeypatch) -> None:
    async def fake_llm(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("planner unavailable")

    monkeypatch.setattr(agent_planning_service, "_call_planner_llm", fake_llm)

    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post("/api/v1/agent-plans", json={}, headers=headers).json()
        response = client.post(
            f"/api/v1/agent-plans/{created['id']}/messages",
            json={"content": "Test API https://api.example.test/openapi.json with read-only checks."},
            headers=headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "collecting"
    assert body["ready_to_execute"] is False
    assert body["current_plan"] is None
    assert any("是否需要鉴权" in message["content"] for message in body["messages"])


def test_latest_user_source_overrides_stale_structured_payload_after_rejection() -> None:
    payload = normalize_planner_run_payload(
        {"source": "https://old.example.test/openapi.json", "test_type": "api"},
        [
            _message("Test https://old.example.test/openapi.json as API."),
            AgentPlanningMessage(
                session_id="test-session",
                role="system",
                content="Plan rejected. Revision reason: use another target.",
            ),
            _message("改用接口 https://new.example.test/openapi.json 做只读检查。"),
        ],
    )

    assert payload.source == "https://new.example.test/openapi.json"
    assert payload.test_type == "api"
    assert payload.api_execution_policy == "safe_read_only"


def test_create_planning_session() -> None:
    with TestClient(app) as client:
        token = _token(client)
        response = client.post(
            "/api/v1/agent-plans",
            json={"title": "Checkout regression"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["id"]
    assert body["title"] == "Checkout regression"
    assert body["status"] == "collecting"
    assert body["messages"] == []


def test_create_agent_plan_session_alias_returns_current_step() -> None:
    with TestClient(app) as client:
        token = _token(client)
        response = client.post(
            "/api/v1/agent-plans/sessions",
            json={"title": "Alias checkout plan"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["id"]
    assert body["title"] == "Alias checkout plan"
    assert body["current_step"] == "target"


def test_list_agent_plan_session_aliases_for_current_user() -> None:
    other_username = f"plan-list-user-{uuid.uuid4().hex}"
    other_password = "other-password"
    first_title = f"Alias list first {uuid.uuid4().hex}"
    second_title = f"Alias list second {uuid.uuid4().hex}"
    other_title = f"Alias list other {uuid.uuid4().hex}"

    with TestClient(app) as client:
        asyncio.run(_create_test_user(other_username, other_password))
        token = _token(client)
        other_token = _login_token(client, other_username, other_password)
        headers = {"Authorization": f"Bearer {token}"}
        other_headers = {"Authorization": f"Bearer {other_token}"}
        first = client.post(
            "/api/v1/agent-plans/sessions",
            json={"title": first_title},
            headers=headers,
        ).json()
        second = client.post(
            "/api/v1/agent-plans/sessions",
            json={"title": second_title},
            headers=headers,
        ).json()
        other = client.post(
            "/api/v1/agent-plans/sessions",
            json={"title": other_title},
            headers=other_headers,
        ).json()
        response = client.get("/api/v1/agent-plans/sessions", headers=headers)

    assert response.status_code == 200
    sessions = response.json()
    ids = {session["id"] for session in sessions}
    assert first["id"] in ids
    assert second["id"] in ids
    assert other["id"] not in ids
    assert all(session["current_step"] for session in sessions)


def test_get_agent_plan_session_alias_returns_owner_details() -> None:
    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post(
            "/api/v1/agent-plans/sessions",
            json={"title": "Alias detail plan"},
            headers=headers,
        ).json()
        response = client.get(
            f"/api/v1/agent-plans/sessions/{created['id']}",
            headers=headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == created["id"]
    assert body["title"] == "Alias detail plan"
    assert body["current_step"] == "target"
    assert body["messages"] == []


def test_get_agent_plan_session_alias_hides_other_user_session() -> None:
    other_username = f"plan-get-user-{uuid.uuid4().hex}"
    other_password = "other-password"

    with TestClient(app) as client:
        asyncio.run(_create_test_user(other_username, other_password))
        token = _token(client)
        other_token = _login_token(client, other_username, other_password)
        headers = {"Authorization": f"Bearer {token}"}
        other_headers = {"Authorization": f"Bearer {other_token}"}
        created = client.post(
            "/api/v1/agent-plans/sessions",
            json={"title": "Alias owner-only plan"},
            headers=headers,
        ).json()
        response = client.get(
            f"/api/v1/agent-plans/sessions/{created['id']}",
            headers=other_headers,
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Planning session not found"


def test_intake_agent_plan_session_missing_source_collects_and_persists_messages(
    monkeypatch,
) -> None:
    async def fake_llm(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("planner unavailable")

    monkeypatch.setattr(agent_planning_service, "_call_planner_llm", fake_llm)

    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post(
            "/api/v1/agent-plans/sessions",
            json={},
            headers=headers,
        ).json()
        response = client.post(
            f"/api/v1/agent-plans/sessions/{created['id']}/intake",
            json={"message": "Run a checkout smoke test."},
            headers=headers,
        )
        detail = client.get(
            f"/api/v1/agent-plans/sessions/{created['id']}",
            headers=headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["session"]["id"] == created["id"]
    assert body["session"]["status"] == "collecting"
    assert body["draft"]["target"]["status"] == "missing"
    assert body["next_question"]["step"] == "target"
    assert body["next_question"]["title"]
    assert any(item["key"] == "target" for item in body["missing_info"])
    assert [message["role"] for message in body["session"]["messages"]] == [
        "user",
        "assistant",
    ]
    assert body["session"]["messages"][0]["content"] == "Run a checkout smoke test."
    assert detail.status_code == 200
    assert [message["role"] for message in detail.json()["messages"]] == [
        "user",
        "assistant",
    ]


def test_intake_agent_plan_session_ready_api_target_returns_payload(
    monkeypatch,
) -> None:
    async def fake_llm(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("planner unavailable")

    monkeypatch.setattr(agent_planning_service, "_call_planner_llm", fake_llm)

    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post(
            "/api/v1/agent-plans/sessions",
            json={},
            headers=headers,
        ).json()
        response = client.post(
            f"/api/v1/agent-plans/sessions/{created['id']}/intake",
            json={"message": "Test API https://api.example.test/openapi.json. no auth."},
            headers=headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["session"]["status"] == "ready"
    assert (
        body["session"]["current_run_payload"]["source"]
        == "https://api.example.test/openapi.json"
    )
    assert body["next_question"] is None


def test_structured_intake_continue_updates_state_without_chat_message() -> None:
    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post(
            "/api/v1/agent-plans/sessions",
            json={},
            headers=headers,
        ).json()
        response = client.post(
            f"/api/v1/agent-plans/sessions/{created['id']}/intake",
            json={
                "action": "continue",
                "current_step": "target_kind",
                "selected_option": {
                    "label": "API / 接口",
                    "value": "api_openapi",
                    "field": "target_kind",
                    "step": "target_kind",
                    "message": "测试目标类型：API / OpenAPI/Swagger 接口来源。",
                },
                "message": "https://api.example.test/openapi.json no auth",
            },
            headers=headers,
        )
        detail = client.get(
            f"/api/v1/agent-plans/sessions/{created['id']}",
            headers=headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["session"]["status"] == "collecting"
    assert body["session"]["current_step"] == "scope"
    assert body["session"]["current_run_payload"] is None
    assert body["session"]["messages"] == []
    assert body["next_question"]["step"] == "scope"
    assert body["draft"]["scope"]["status"] == "pending"
    assert body["draft"]["auth"]["status"] == "pending"
    assert body["draft"]["safety"]["status"] == "pending"
    assert detail.status_code == 200
    assert detail.json()["messages"] == []


def test_structured_intake_target_choice_advances_to_next_missing_step() -> None:
    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post(
            "/api/v1/agent-plans/sessions",
            json={},
            headers=headers,
        ).json()
        response = client.post(
            f"/api/v1/agent-plans/sessions/{created['id']}/intake",
            json={
                "action": "continue",
                "current_step": "target_kind",
                "selected_option": {
                    "label": "Web UI / 网页",
                    "value": "web_page",
                    "field": "target_kind",
                    "step": "target_kind",
                    "message": "测试目标类型：浏览器 Web UI 页面。",
                },
            },
            headers=headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["session"]["status"] == "collecting"
    assert body["session"]["messages"] == []
    assert body["next_question"]["step"] == "scope"
    assert body["missing_info"][0]["key"] == "scope"


def test_structured_intake_rejects_required_step_skip() -> None:
    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post(
            "/api/v1/agent-plans/sessions",
            json={},
            headers=headers,
        ).json()
        response = client.post(
            f"/api/v1/agent-plans/sessions/{created['id']}/intake",
            json={"action": "skip", "current_step": "target_kind"},
            headers=headers,
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "current_step cannot be skipped"


def test_structured_intake_revisits_missing_target_after_other_steps() -> None:
    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post(
            "/api/v1/agent-plans/sessions",
            json={},
            headers=headers,
        ).json()
        session_id = created["id"]
        client.post(
            f"/api/v1/agent-plans/sessions/{session_id}/intake",
            json={
                "action": "continue",
                "current_step": "target_kind",
                "selected_option": {
                    "label": "API / 接口",
                    "value": "api_openapi",
                    "field": "target_kind",
                    "step": "target_kind",
                    "message": "测试目标类型：API / OpenAPI/Swagger 接口来源。",
                },
            },
            headers=headers,
        )
        client.post(
            f"/api/v1/agent-plans/sessions/{session_id}/intake",
            json={"action": "skip", "current_step": "coverage_scope"},
            headers=headers,
        )
        client.post(
            f"/api/v1/agent-plans/sessions/{session_id}/intake",
            json={
                "action": "continue",
                "current_step": "auth_boundary",
                "selected_option": {
                    "label": "无需登录",
                    "value": "no_auth",
                    "field": "auth_boundary",
                    "step": "auth_boundary",
                    "message": "登录方式/凭证：目标公开访问，无需登录或鉴权。",
                },
            },
            headers=headers,
        )
        client.post(
            f"/api/v1/agent-plans/sessions/{session_id}/intake",
            json={
                "action": "continue",
                "current_step": "safety_boundary",
                "selected_option": {
                    "label": "只读边界",
                    "value": "safe_read_only",
                    "field": "safety_boundary",
                    "step": "safety_boundary",
                    "message": "安全边界：只做只读检查，不创建、修改或删除数据。",
                },
            },
            headers=headers,
        )
        response = client.post(
            f"/api/v1/agent-plans/sessions/{session_id}/intake",
            json={"action": "skip", "current_step": "success_criteria"},
            headers=headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["session"]["status"] == "collecting"
    assert body["session"]["current_step"] == "target"
    assert body["next_question"]["step"] == "target"
    assert body["missing_info"][0]["key"] == "target"


def test_structured_intake_target_supplement_survives_until_ready() -> None:
    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post(
            "/api/v1/agent-plans/sessions",
            json={},
            headers=headers,
        ).json()
        session_id = created["id"]
        target = client.post(
            f"/api/v1/agent-plans/sessions/{session_id}/intake",
            json={
                "action": "continue",
                "current_step": "target_kind",
                "selected_option": {
                    "label": "API / 接口",
                    "value": "api_openapi",
                    "field": "target_kind",
                    "step": "target_kind",
                    "message": "测试目标类型：API / OpenAPI/Swagger 接口来源。",
                },
                "message": "请测试 https://httpbin.org/get 响应需要包含 url 字段，状态码 200",
            },
            headers=headers,
        )
        client.post(
            f"/api/v1/agent-plans/sessions/{session_id}/intake",
            json={"action": "skip", "current_step": "coverage_scope"},
            headers=headers,
        )
        client.post(
            f"/api/v1/agent-plans/sessions/{session_id}/intake",
            json={
                "action": "continue",
                "current_step": "auth_boundary",
                "selected_option": {
                    "label": "无需登录",
                    "value": "no_auth",
                    "field": "auth_boundary",
                    "step": "auth_boundary",
                    "message": "登录方式/凭证：目标公开访问，无需登录或鉴权。",
                },
            },
            headers=headers,
        )
        client.post(
            f"/api/v1/agent-plans/sessions/{session_id}/intake",
            json={
                "action": "continue",
                "current_step": "safety_boundary",
                "selected_option": {
                    "label": "只读边界",
                    "value": "safe_read_only",
                    "field": "safety_boundary",
                    "step": "safety_boundary",
                    "message": "安全边界：只做只读检查，不创建、修改或删除数据。",
                },
            },
            headers=headers,
        )
        response = client.post(
            f"/api/v1/agent-plans/sessions/{session_id}/intake",
            json={"action": "skip", "current_step": "success_criteria"},
            headers=headers,
        )

    assert target.status_code == 200
    assert target.json()["draft"]["target"]["value"]
    assert "https://httpbin.org/get" in target.json()["draft"]["target"]["value"]
    assert response.status_code == 200
    body = response.json()
    assert body["session"]["status"] == "ready"
    assert body["session"]["current_step"] == "review"
    assert body["session"]["current_run_payload"]["source"] == "https://httpbin.org/get"
    assert body["next_question"] is None
    assert body["missing_info"] == []


def test_structured_intake_multi_url_target_supplement_builds_schema_source() -> None:
    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post(
            "/api/v1/agent-plans/sessions",
            json={},
            headers=headers,
        ).json()
        session_id = created["id"]
        target = client.post(
            f"/api/v1/agent-plans/sessions/{session_id}/intake",
            json={
                "action": "continue",
                "current_step": "target_kind",
                "selected_option": {
                    "label": "API / 接口",
                    "value": "api_openapi",
                    "field": "target_kind",
                    "step": "target_kind",
                    "message": "测试目标类型：API / OpenAPI/Swagger 接口来源。",
                },
                "message": (
                    "请测试 https://httpbin.org/get 和 https://httpbin.org/headers "
                    "两个只读接口，响应 JSON 需要包含 url、headers 字段。"
                ),
            },
            headers=headers,
        )
        client.post(
            f"/api/v1/agent-plans/sessions/{session_id}/intake",
            json={"action": "skip", "current_step": "coverage_scope"},
            headers=headers,
        )
        client.post(
            f"/api/v1/agent-plans/sessions/{session_id}/intake",
            json={
                "action": "continue",
                "current_step": "auth_boundary",
                "selected_option": {
                    "label": "无需登录",
                    "value": "no_auth",
                    "field": "auth_boundary",
                    "step": "auth_boundary",
                    "message": "登录方式/凭证：目标公开访问，无需登录或鉴权。",
                },
            },
            headers=headers,
        )
        client.post(
            f"/api/v1/agent-plans/sessions/{session_id}/intake",
            json={
                "action": "continue",
                "current_step": "safety_boundary",
                "selected_option": {
                    "label": "只读边界",
                    "value": "safe_read_only",
                    "field": "safety_boundary",
                    "step": "safety_boundary",
                    "message": "安全边界：只做只读检查，不创建、修改或删除数据。",
                },
            },
            headers=headers,
        )
        response = client.post(
            f"/api/v1/agent-plans/sessions/{session_id}/intake",
            json={"action": "skip", "current_step": "success_criteria"},
            headers=headers,
        )

    assert target.status_code == 200
    assert target.json()["draft"]["target"]["value"]
    assert response.status_code == 200
    body = response.json()
    assert body["session"]["status"] == "ready"
    source = body["session"]["current_run_payload"]["source"]
    document = json.loads(source)
    assert document["servers"] == [{"url": "https://httpbin.org"}]
    assert set(document["paths"]) == {"/get", "/headers"}
    assert document["paths"]["/headers"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["required"] == ["url", "headers"]
    assert document["paths"]["/headers"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["x-testclaw-user-required-fields"] is True


def test_generate_agent_plan_session_before_ready_returns_400() -> None:
    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post(
            "/api/v1/agent-plans/sessions",
            json={"title": "Not ready generate plan"},
            headers=headers,
        ).json()
        response = client.post(
            f"/api/v1/agent-plans/sessions/{created['id']}/generate",
            headers=headers,
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "No executable plan is ready"


def test_generate_agent_plan_session_after_ready_api_intake_returns_plan_payload(
    monkeypatch,
) -> None:
    async def fake_llm(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("planner unavailable")

    monkeypatch.setattr(agent_planning_service, "_call_planner_llm", fake_llm)

    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post(
            "/api/v1/agent-plans/sessions",
            json={},
            headers=headers,
        ).json()
        ready = client.post(
            f"/api/v1/agent-plans/sessions/{created['id']}/intake",
            json={"message": "Test API https://api.example.test/openapi.json. no auth."},
            headers=headers,
        )
        response = client.post(
            f"/api/v1/agent-plans/sessions/{created['id']}/generate",
            headers=headers,
        )

    assert ready.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body["plan_id"] == created["id"]
    assert body["status"] == "ready"
    assert body["summary"]
    assert body["recommended_run_payload"]["source"] == "https://api.example.test/openapi.json"
    assert body["api_plan"]
    assert body["ui_plan"] == {}
    assert body["session"]["id"] == created["id"]


def test_generate_agent_plan_session_hides_other_user_session() -> None:
    other_username = f"plan-generate-user-{uuid.uuid4().hex}"
    other_password = "other-password"

    with TestClient(app) as client:
        asyncio.run(_create_test_user(other_username, other_password))
        token = _token(client)
        other_token = _login_token(client, other_username, other_password)
        headers = {"Authorization": f"Bearer {token}"}
        other_headers = {"Authorization": f"Bearer {other_token}"}
        created = client.post(
            "/api/v1/agent-plans/sessions",
            json={"title": "Owner generate plan"},
            headers=headers,
        ).json()
        response = client.post(
            f"/api/v1/agent-plans/sessions/{created['id']}/generate",
            headers=other_headers,
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Planning session not found"


def test_intake_agent_plan_session_hides_other_user_session(monkeypatch) -> None:
    async def fake_llm(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("planner unavailable")

    monkeypatch.setattr(agent_planning_service, "_call_planner_llm", fake_llm)

    other_username = f"plan-intake-user-{uuid.uuid4().hex}"
    other_password = "other-password"

    with TestClient(app) as client:
        asyncio.run(_create_test_user(other_username, other_password))
        token = _token(client)
        other_token = _login_token(client, other_username, other_password)
        headers = {"Authorization": f"Bearer {token}"}
        other_headers = {"Authorization": f"Bearer {other_token}"}
        created = client.post(
            "/api/v1/agent-plans/sessions",
            json={"title": "Owner intake plan"},
            headers=headers,
        ).json()
        response = client.post(
            f"/api/v1/agent-plans/sessions/{created['id']}/intake",
            json={"message": "Test API https://api.example.test/openapi.json."},
            headers=other_headers,
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Planning session not found"


def test_intake_agent_plan_session_empty_payload_requires_message() -> None:
    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post(
            "/api/v1/agent-plans/sessions",
            json={},
            headers=headers,
        ).json()
        response = client.post(
            f"/api/v1/agent-plans/sessions/{created['id']}/intake",
            json={},
            headers=headers,
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "message is required"


def test_planning_message_asks_missing_source(monkeypatch) -> None:
    async def fake_llm(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("planner unavailable")

    monkeypatch.setattr(agent_planning_service, "_call_planner_llm", fake_llm)

    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post("/api/v1/agent-plans", json={}, headers=headers).json()
        response = client.post(
            f"/api/v1/agent-plans/{created['id']}/messages",
            json={"content": "Run a checkout smoke test."},
            headers=headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "collecting"
    assert body["ready_to_execute"] is False
    assert body["current_plan"] is None
    assert any("目标" in message["content"] for message in body["messages"])
    assert body["question_options"]
    assert len(body["question_options"]) == 1
    assert body["messages"][-1]["plan"]["question_options"] == body["question_options"]
    assert all(group["question"] for group in body["question_options"])
    assert all(
        option["label"] and option["message"]
        for group in body["question_options"]
        for option in group["options"]
    )
    assert all(
        option.get("field") and option.get("value")
        for group in body["question_options"]
        for option in group["options"]
    )
    assert any(
        option["label"] == "自定义"
        for group in body["question_options"]
        for option in group["options"]
    )
    _assert_no_placeholder_option_messages(body["question_options"])


def test_planning_message_exposes_model_provided_choice_options(monkeypatch) -> None:
    async def fake_llm(*args: Any, **kwargs: Any) -> PlannerLLMOutput:
        return PlannerLLMOutput(
            response="请选择本轮范围。",
            status="collecting",
            questions=["希望先覆盖哪个测试范围？"],
            question_options=[
                {
                    "question": "希望先覆盖哪个测试范围？",
                    "options": [
                        {
                            "label": "关键路径",
                            "message": "范围：先覆盖关键路径和发布阻断风险。",
                        }
                    ],
                }
            ],
            ready_to_execute=False,
            run_payload={},
        )

    monkeypatch.setattr(agent_planning_service, "_call_planner_llm", fake_llm)

    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post("/api/v1/agent-plans", json={}, headers=headers).json()
        response = client.post(
            f"/api/v1/agent-plans/{created['id']}/messages",
            json={"content": "先帮我规划测试范围。"},
            headers=headers,
        )

    assert response.status_code == 200
    question_options = response.json()["question_options"]
    assert len(question_options) == 1
    assert question_options[0]["question"] == "希望先覆盖哪个测试范围？"
    assert question_options[0]["step"] == "coverage_scope"
    assert question_options[0]["options"][0]["label"] == "关键路径"
    assert question_options[0]["options"][0]["message"] == "范围：先覆盖关键路径和发布阻断风险。"
    assert question_options[0]["options"][0]["field"] == "coverage_scope"
    assert question_options[0]["options"][-1]["label"] == "自定义"
    assert question_options[0]["options"][-1]["value"] == "custom"
    _assert_no_placeholder_option_messages(question_options)


def test_planning_message_limits_model_provided_choice_groups(monkeypatch) -> None:
    async def fake_llm(*args: Any, **kwargs: Any) -> PlannerLLMOutput:
        return PlannerLLMOutput(
            response="请选择本轮规划方式。",
            status="collecting",
            questions=["请选择本轮规划方式。"],
            question_options=[
                {
                    "question": "先按哪个目标类型规划？",
                    "options": [
                        {
                            "label": "接口",
                            "message": "我要测试 API 或 OpenAPI/Swagger 来源。",
                        }
                    ],
                },
                {
                    "question": "先按哪个测试范围规划？",
                    "options": [
                        {
                            "label": "冒烟",
                            "message": "范围：先做关键路径冒烟检查。",
                        }
                    ],
                },
                {
                    "question": "安全边界是什么？",
                    "options": [
                        {
                            "label": "只读",
                            "message": "安全边界：只做只读检查。",
                        }
                    ],
                },
            ],
            ready_to_execute=False,
            run_payload={},
        )

    monkeypatch.setattr(agent_planning_service, "_call_planner_llm", fake_llm)

    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post("/api/v1/agent-plans", json={}, headers=headers).json()
        response = client.post(
            f"/api/v1/agent-plans/{created['id']}/messages",
            json={"content": "先帮我规划测试方式。"},
            headers=headers,
        )

    assert response.status_code == 200
    question_options = response.json()["question_options"]
    assert len(question_options) == 2
    assert [group["question"] for group in question_options] == [
        "先按哪个目标类型规划？",
        "先按哪个测试范围规划？",
    ]
    assert all(
        any(option["label"] == "自定义" for option in group["options"])
        for group in question_options
    )
    _assert_no_placeholder_option_messages(question_options)


def test_planning_message_filters_unsupported_target_choice_options(monkeypatch) -> None:
    async def fake_llm(*args: Any, **kwargs: Any) -> PlannerLLMOutput:
        return PlannerLLMOutput(
            response="请选择要测试的目标类型。",
            status="collecting",
            questions=["请选择要测试的目标类型。"],
            question_options=[
                {
                    "question": "请选择要测试的目标类型。",
                    "options": [
                        {"label": "桌面软件", "message": "我要测试桌面软件客户端。"},
                        {"label": "手机 App", "message": "我要测试 native mobile app。"},
                        {"label": "Native app", "message": "Test an iOS app."},
                    ],
                }
            ],
            ready_to_execute=False,
            run_payload={},
        )

    monkeypatch.setattr(agent_planning_service, "_call_planner_llm", fake_llm)

    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post("/api/v1/agent-plans", json={}, headers=headers).json()
        response = client.post(
            f"/api/v1/agent-plans/{created['id']}/messages",
            json={"content": "先规划一个测试目标。"},
            headers=headers,
        )

    assert response.status_code == 200
    body = response.json()
    question_options = body["question_options"]
    serialized = response.text
    assert "桌面软件" not in serialized
    assert "手机 App" not in serialized
    assert "native mobile" not in serialized
    assert "Native app" not in serialized
    assert body["messages"][-1]["plan"]["question_options"] == question_options
    assert question_options[0]["question"] == "请选择要测试的目标类型。"
    assert question_options[0]["step"] == "target_kind"
    assert [option["value"] for option in question_options[0]["options"]] == [
        "api_openapi",
        "web_page",
        "custom",
    ]
    assert [option["label"] for option in question_options[0]["options"]] == [
        "API / 接口",
        "Web UI / 网页",
        "自定义",
    ]
    _assert_no_placeholder_option_messages(question_options)


def test_planning_message_filters_placeholder_choice_messages(monkeypatch) -> None:
    async def fake_llm(*args: Any, **kwargs: Any) -> PlannerLLMOutput:
        return PlannerLLMOutput(
            response="请选择要测试的目标类型。",
            status="collecting",
            questions=["请选择要测试的目标类型。"],
            question_options=[
                {
                    "question": "请选择要测试的目标类型。",
                    "step": "target_kind",
                    "options": [
                        {
                            "label": "接口",
                            "message": "我要测试 API 或 OpenAPI/Swagger 来源，稍后补充具体地址。",
                        },
                        {
                            "label": "网页",
                            "message": "我要测试网页 UI，稍后补充具体地址。",
                        },
                        {
                            "label": "粘贴目标",
                            "message": "我会直接粘贴目标 URL 或 OpenAPI/Swagger 来源。",
                        },
                        {
                            "label": "补充说明",
                            "message": "我会补充关于“要先确定哪类测试目标”的具体说明。",
                        },
                    ],
                }
            ],
            ready_to_execute=False,
            run_payload={},
        )

    monkeypatch.setattr(agent_planning_service, "_call_planner_llm", fake_llm)

    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post("/api/v1/agent-plans", json={}, headers=headers).json()
        response = client.post(
            f"/api/v1/agent-plans/{created['id']}/messages",
            json={"content": "先规划一个测试目标。"},
            headers=headers,
        )

    assert response.status_code == 200
    serialized = response.text
    assert "稍后补充具体地址" not in serialized
    assert "我会补充关于" not in serialized
    assert "我会直接粘贴目标 URL" not in serialized
    question_options = response.json()["question_options"]
    assert [option["value"] for option in question_options[0]["options"]] == [
        "api_openapi",
        "web_page",
        "custom",
    ]
    _assert_no_placeholder_option_messages(question_options)


def test_planning_message_times_out_slow_llm_and_uses_fallback(monkeypatch) -> None:
    async def slow_llm(*args: Any, **kwargs: Any) -> PlannerLLMOutput:
        await asyncio.sleep(1)
        return PlannerLLMOutput(response="too slow", status="collecting")

    monkeypatch.setattr(agent_planning_service, "_call_planner_llm", slow_llm)
    monkeypatch.setattr(settings, "AGENT_PLAN_LLM_TIMEOUT_SECONDS", 0.01)

    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post("/api/v1/agent-plans", json={}, headers=headers).json()
        response = client.post(
            f"/api/v1/agent-plans/{created['id']}/messages",
            json={"content": "Test API https://api-timeout.example.test/openapi.json. no auth."},
            headers=headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["current_run_payload"]["source"] == "https://api-timeout.example.test/openapi.json"
    assert "too slow" not in body["messages"][-1]["content"]


def test_whitespace_planning_message_is_rejected_and_not_stored() -> None:
    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post("/api/v1/agent-plans", json={}, headers=headers).json()
        response = client.post(
            f"/api/v1/agent-plans/{created['id']}/messages",
            json={"content": "   \n\t  "},
            headers=headers,
        )
        detail = client.get(f"/api/v1/agent-plans/{created['id']}", headers=headers)

    assert response.status_code == 400
    assert response.json()["detail"] == "content is required"
    assert detail.status_code == 200
    assert detail.json()["messages"] == []


def test_planning_message_returns_ready_plan_with_redacted_payload(monkeypatch) -> None:
    async def fake_llm(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("planner unavailable")

    monkeypatch.setattr(agent_planning_service, "_call_planner_llm", fake_llm)

    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post("/api/v1/agent-plans", json={}, headers=headers).json()
        response = client.post(
            f"/api/v1/agent-plans/{created['id']}/messages",
            json={
                "content": (
                    "Test API https://api.example.test/openapi.json with token=secret-token "
                    "and objective health regression."
                )
            },
            headers=headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["ready_to_execute"] is True
    assert body["current_plan"]["test_type"] == "api"
    assert body["current_run_payload"]["source"] == "https://api.example.test/openapi.json"
    assert body["current_run_payload"]["token"] == "[REDACTED]"
    assert "secret-token" not in response.text


def test_ui_plan_without_auth_boundary_keeps_collecting(monkeypatch) -> None:
    async def fake_llm(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("planner unavailable")

    monkeypatch.setattr(agent_planning_service, "_call_planner_llm", fake_llm)

    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post("/api/v1/agent-plans", json={}, headers=headers).json()
        response = client.post(
            f"/api/v1/agent-plans/{created['id']}/messages",
            json={"content": "测试页面 https://app.internal.test ，只做 UI 只读冒烟检查。"},
            headers=headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "collecting"
    assert body["ready_to_execute"] is False
    assert body["current_plan"] is None
    assert any("是否需要登录" in message["content"] for message in body["messages"])


def test_ui_plan_with_no_auth_confirmation_is_ready(monkeypatch) -> None:
    async def fake_llm(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("planner unavailable")

    monkeypatch.setattr(agent_planning_service, "_call_planner_llm", fake_llm)

    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post("/api/v1/agent-plans", json={}, headers=headers).json()
        response = client.post(
            f"/api/v1/agent-plans/{created['id']}/messages",
            json={"content": "测试公开页面 https://example.com ，UI 只读冒烟检查，无需登录。"},
            headers=headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["current_run_payload"]["test_type"] == "ui"
    assert body["current_run_payload"]["auth_mode"] == "none_confirmed"


def test_ui_auth_boundary_followup_preserves_ui_mode(monkeypatch) -> None:
    async def fake_llm(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("planner unavailable")

    monkeypatch.setattr(agent_planning_service, "_call_planner_llm", fake_llm)

    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post("/api/v1/agent-plans", json={}, headers=headers).json()
        first = client.post(
            f"/api/v1/agent-plans/{created['id']}/messages",
            json={"content": "测试页面 https://app.internal.test ，只做 UI 只读冒烟检查。"},
            headers=headers,
        )
        followup = client.post(
            f"/api/v1/agent-plans/{created['id']}/messages",
            json={"content": "确认无需登录。"},
            headers=headers,
        )

    assert first.status_code == 200
    assert first.json()["status"] == "collecting"
    assert followup.status_code == 200
    body = followup.json()
    assert body["status"] == "ready"
    assert body["current_run_payload"]["test_type"] == "ui"
    assert body["current_run_payload"]["source"] == "https://app.internal.test"
    assert body["current_run_payload"]["auth_mode"] == "none_confirmed"


def test_reject_then_regenerate_plan(monkeypatch) -> None:
    async def fake_llm(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("planner unavailable")

    monkeypatch.setattr(agent_planning_service, "_call_planner_llm", fake_llm)

    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post("/api/v1/agent-plans", json={}, headers=headers).json()
        ready = client.post(
            f"/api/v1/agent-plans/{created['id']}/messages",
            json={"content": "Test public page https://app.example.test as a UI smoke. no auth."},
            headers=headers,
        )
        assert ready.status_code == 200

        rejected = client.post(
            f"/api/v1/agent-plans/{created['id']}/reject",
            json={"reason": "Use API mode instead."},
            headers=headers,
        )
        assert rejected.status_code == 200
        rejected_body = rejected.json()
        assert rejected_body["status"] == "collecting"
        assert rejected_body["current_plan"] is None
        assert rejected_body["rejection_reason"] == "Use API mode instead."

        regenerated = client.post(
            f"/api/v1/agent-plans/{created['id']}/messages",
            json={"content": "Use https://api.example.test/openapi.json for API read-only checks. no auth."},
            headers=headers,
        )

    assert regenerated.status_code == 200
    body = regenerated.json()
    assert body["status"] == "ready"
    assert body["current_run_payload"]["test_type"] == "api"
    assert body["current_run_payload"]["source"] == "https://api.example.test/openapi.json"
    assert "UI" not in body["current_run_payload"]["objective"]
    assert "Use API mode instead" not in body["current_run_payload"]["objective"]
    assert "UI" not in body["current_run_payload"]["setup_instructions"]
    assert "Use API mode instead" not in body["current_run_payload"]["setup_instructions"]


def test_create_run_after_ready_api_intake_marks_executed_and_returns_link(
    monkeypatch,
) -> None:
    execute_payloads: list[dict[str, Any]] = []

    async def fake_llm(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("planner unavailable")

    async def fake_execute(payload: dict[str, Any], db: Any, user: Any) -> Task:
        execute_payloads.append(payload)
        task = Task(
            objective=payload["objective"],
            target_url=payload["source"],
            status=TaskStatus.QUEUED,
            test_type=TaskTestType.API,
            retry_count=0,
            execution_log=json.dumps({}),
            created_at=datetime.utcnow(),
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        return task

    monkeypatch.setattr(agent_planning_service, "_call_planner_llm", fake_llm)
    monkeypatch.setattr(agent_plans, "_execute_run_payload", fake_execute)

    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post(
            "/api/v1/agent-plans/sessions",
            json={},
            headers=headers,
        ).json()
        ready = client.post(
            f"/api/v1/agent-plans/sessions/{created['id']}/intake",
            json={"message": "Test API https://api.example.test/openapi.json. no auth."},
            headers=headers,
        )
        response = client.post(
            f"/api/v1/agent-plans/{created['id']}/create-run",
            headers=headers,
        )
        idempotent_response = client.post(
            f"/api/v1/agent-plans/{created['id']}/create-run",
            headers=headers,
        )
        session_response = client.get(
            f"/api/v1/agent-plans/sessions/{created['id']}",
            headers=headers,
        )

    assert ready.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"]
    assert body["detail_url"] == f"/runs/{body['run_id']}"
    assert idempotent_response.status_code == 200
    assert idempotent_response.json() == body
    assert len(execute_payloads) == 1
    assert execute_payloads[0]["source"] == "https://api.example.test/openapi.json"
    session_body = session_response.json()
    assert session_body["status"] == "executed"
    assert session_body["current_step"] == "executed"
    assert session_body["executed_run_id"] == body["run_id"]


def test_execute_after_create_run_reuses_existing_run(monkeypatch) -> None:
    execute_payloads: list[dict[str, Any]] = []

    async def fake_llm(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("planner unavailable")

    async def fake_execute(payload: dict[str, Any], db: Any, user: Any) -> Task:
        execute_payloads.append(payload)
        task = Task(
            objective=payload["objective"],
            target_url=payload["source"],
            status=TaskStatus.QUEUED,
            test_type=TaskTestType.API,
            retry_count=0,
            execution_log=json.dumps({}),
            created_at=datetime.utcnow(),
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        return task

    monkeypatch.setattr(agent_planning_service, "_call_planner_llm", fake_llm)
    monkeypatch.setattr(agent_plans, "_execute_run_payload", fake_execute)

    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post(
            "/api/v1/agent-plans/sessions",
            json={},
            headers=headers,
        ).json()
        ready = client.post(
            f"/api/v1/agent-plans/sessions/{created['id']}/intake",
            json={"message": "Test API https://api.example.test/openapi.json. no auth."},
            headers=headers,
        )
        created_run = client.post(
            f"/api/v1/agent-plans/{created['id']}/create-run",
            headers=headers,
        )
        legacy_execute = client.post(
            f"/api/v1/agent-plans/{created['id']}/execute",
            headers=headers,
        )

    assert ready.status_code == 200
    assert created_run.status_code == 200
    assert legacy_execute.status_code == 200
    assert legacy_execute.json()["run"]["id"] == created_run.json()["run_id"]
    assert legacy_execute.json()["session"]["executed_run_id"] == created_run.json()["run_id"]
    assert len(execute_payloads) == 1


def test_create_run_before_ready_returns_400() -> None:
    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post(
            "/api/v1/agent-plans/sessions",
            json={"title": "Not ready create run"},
            headers=headers,
        ).json()
        response = client.post(
            f"/api/v1/agent-plans/{created['id']}/create-run",
            headers=headers,
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "No executable plan is ready"


def test_create_run_hides_other_user_session() -> None:
    other_username = f"plan-create-run-user-{uuid.uuid4().hex}"
    other_password = "other-password"

    with TestClient(app) as client:
        asyncio.run(_create_test_user(other_username, other_password))
        token = _token(client)
        other_token = _login_token(client, other_username, other_password)
        headers = {"Authorization": f"Bearer {token}"}
        other_headers = {"Authorization": f"Bearer {other_token}"}
        created = client.post(
            "/api/v1/agent-plans/sessions",
            json={"title": "Owner create run plan"},
            headers=headers,
        ).json()
        response = client.post(
            f"/api/v1/agent-plans/{created['id']}/create-run",
            headers=other_headers,
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Planning session not found"


def test_execute_current_plan_uses_run_creation_path(monkeypatch) -> None:
    async def fake_llm(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("planner unavailable")

    async def fake_execute(payload: dict[str, Any], db: Any, user: Any) -> Task:
        task = Task(
            objective=payload["objective"],
            target_url=payload["source"],
            status=TaskStatus.QUEUED,
            test_type=TaskTestType.API,
            retry_count=0,
            execution_log=json.dumps({}),
            created_at=datetime.utcnow(),
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        return task

    monkeypatch.setattr(agent_planning_service, "_call_planner_llm", fake_llm)
    monkeypatch.setattr(agent_plans, "_execute_run_payload", fake_execute)

    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post("/api/v1/agent-plans", json={}, headers=headers).json()
        ready = client.post(
            f"/api/v1/agent-plans/{created['id']}/messages",
            json={"content": "Test API https://api.example.test/openapi.json. no auth."},
            headers=headers,
        )
        assert ready.status_code == 200
        first_user_id = ready.json()["messages"][0]["id"]
        response = client.post(
            f"/api/v1/agent-plans/{created['id']}/execute",
            headers=headers,
        )
        add_after_execute = client.post(
            f"/api/v1/agent-plans/{created['id']}/messages",
            json={"content": "change after launch"},
            headers=headers,
        )
        edit_after_execute = client.put(
            f"/api/v1/agent-plans/{created['id']}/messages/{first_user_id}",
            json={"content": "change after launch"},
            headers=headers,
        )
        delete_after_execute = client.delete(
            f"/api/v1/agent-plans/{created['id']}/messages/{first_user_id}",
            headers=headers,
        )
        rejected_after_execute = client.post(
            f"/api/v1/agent-plans/{created['id']}/reject",
            json={"reason": "change after launch"},
            headers=headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["run"]["id"]
    assert body["run"]["status"] == "queued"
    assert body["session"]["status"] == "executed"
    assert body["session"]["executed_run_id"] == body["run"]["id"]
    assert add_after_execute.status_code == 400
    assert add_after_execute.json()["detail"] == "Executed plan cannot be changed"
    assert edit_after_execute.status_code == 400
    assert edit_after_execute.json()["detail"] == "Executed plan cannot be changed"
    assert delete_after_execute.status_code == 400
    assert delete_after_execute.json()["detail"] == "Executed plan cannot be changed"
    assert rejected_after_execute.status_code == 400
    assert rejected_after_execute.json()["detail"] == "Executed plan cannot be rejected"


def test_delete_planning_session_removes_conversation(monkeypatch) -> None:
    async def fake_llm(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("planner unavailable")

    monkeypatch.setattr(agent_planning_service, "_call_planner_llm", fake_llm)

    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post("/api/v1/agent-plans", json={}, headers=headers).json()
        ready = client.post(
            f"/api/v1/agent-plans/{created['id']}/messages",
            json={"content": "Test API https://api.example.test/openapi.json."},
            headers=headers,
        )
        assert ready.status_code == 200

        deleted = client.delete(f"/api/v1/agent-plans/{created['id']}", headers=headers)
        fetched = client.get(f"/api/v1/agent-plans/{created['id']}", headers=headers)
        listed = client.get("/api/v1/agent-plans", headers=headers)

    assert deleted.status_code == 204
    assert fetched.status_code == 404
    assert all(item["id"] != created["id"] for item in listed.json())


def test_delete_planning_session_removes_structured_plan_state() -> None:
    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post(
            "/api/v1/agent-plans/sessions",
            json={},
            headers=headers,
        ).json()
        response = client.post(
            f"/api/v1/agent-plans/sessions/{created['id']}/intake",
            json={
                "action": "continue",
                "current_step": "target_kind",
                "selected_option": {
                    "label": "API / 接口",
                    "value": "api_openapi",
                    "field": "target_kind",
                    "step": "target_kind",
                    "message": "测试目标类型：API / OpenAPI/Swagger 接口来源。",
                },
            },
            headers=headers,
        )
        plan_count_before = asyncio.run(_count_structured_agent_plans(created["id"]))
        deleted = client.delete(f"/api/v1/agent-plans/{created['id']}", headers=headers)
        plan_count_after = asyncio.run(_count_structured_agent_plans(created["id"]))

    assert response.status_code == 200
    assert plan_count_before == 1
    assert deleted.status_code == 204
    assert plan_count_after == 0


def test_delete_planning_message_rolls_back_following_messages(monkeypatch) -> None:
    async def fake_llm(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("planner unavailable")

    monkeypatch.setattr(agent_planning_service, "_call_planner_llm", fake_llm)

    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post("/api/v1/agent-plans", json={}, headers=headers).json()
        first = client.post(
            f"/api/v1/agent-plans/{created['id']}/messages",
            json={"content": "Test API https://api.example.test/openapi.json."},
            headers=headers,
        )
        second = client.post(
            f"/api/v1/agent-plans/{created['id']}/messages",
            json={"content": "Focus on contract assertions too."},
            headers=headers,
        )
        assert first.status_code == 200
        assert second.status_code == 200
        first_user_id = first.json()["messages"][0]["id"]

        deleted = client.delete(
            f"/api/v1/agent-plans/{created['id']}/messages/{first_user_id}",
            headers=headers,
        )

    assert deleted.status_code == 200
    body = deleted.json()
    assert body["messages"] == []
    assert body["status"] == "collecting"
    assert body["title"] == "新计划"
    assert body["current_plan"] is None
    assert body["current_run_payload"] is None


def test_edit_prior_user_message_rolls_back_and_regenerates(monkeypatch) -> None:
    async def fake_llm(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("planner unavailable")

    monkeypatch.setattr(agent_planning_service, "_call_planner_llm", fake_llm)

    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post("/api/v1/agent-plans", json={}, headers=headers).json()
        first = client.post(
            f"/api/v1/agent-plans/{created['id']}/messages",
            json={"content": "Test public page https://old.example.test as UI. no auth."},
            headers=headers,
        )
        second = client.post(
            f"/api/v1/agent-plans/{created['id']}/messages",
            json={"content": "Also include the dashboard."},
            headers=headers,
        )
        assert first.status_code == 200
        assert second.status_code == 200
        first_user_id = first.json()["messages"][0]["id"]

        edited = client.put(
            f"/api/v1/agent-plans/{created['id']}/messages/{first_user_id}",
            json={"content": "Test API https://new.example.test/openapi.json with read-only checks. no auth."},
            headers=headers,
        )

    assert edited.status_code == 200
    body = edited.json()
    assert len(body["messages"]) == 2
    assert body["messages"][0]["content"].startswith("Test API https://new.example.test")
    assert all("dashboard" not in message["content"] for message in body["messages"])
    assert body["title"] == "https://new.example.test/openapi.json"
    assert body["status"] == "ready"
    assert body["current_run_payload"]["source"] == "https://new.example.test/openapi.json"
    assert body["current_run_payload"]["test_type"] == "api"
    assert body["current_plan"]["auth_summary"]
    assert body["current_plan"]["auth_summary"] != "[REDACTED]"
    assert "auth" not in body["current_plan"]


def test_stream_edit_validates_message_before_opening_stream(monkeypatch) -> None:
    async def fake_llm(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("planner unavailable")

    monkeypatch.setattr(agent_planning_service, "_call_planner_llm", fake_llm)

    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post("/api/v1/agent-plans", json={}, headers=headers).json()
        ready = client.post(
            f"/api/v1/agent-plans/{created['id']}/messages",
            json={"content": "Test API https://api.example.test/openapi.json."},
            headers=headers,
        )
        assert ready.status_code == 200
        assistant_id = ready.json()["messages"][1]["id"]

        missing = client.put(
            f"/api/v1/agent-plans/{created['id']}/messages/missing-message/stream",
            json={"content": "new content"},
            headers=headers,
        )
        assistant_edit = client.put(
            f"/api/v1/agent-plans/{created['id']}/messages/{assistant_id}/stream",
            json={"content": "new content"},
            headers=headers,
        )

    assert missing.status_code == 404
    assert missing.json()["detail"] == "Planning message not found"
    assert assistant_edit.status_code == 400
    assert assistant_edit.json()["detail"] == "Only user messages can be edited"


def test_stream_planning_message_emits_process_deltas_and_final_session(monkeypatch) -> None:
    async def fake_llm(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("planner unavailable")

    monkeypatch.setattr(agent_planning_service, "_call_planner_llm", fake_llm)

    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post("/api/v1/agent-plans", json={}, headers=headers).json()
        with client.stream(
            "POST",
            f"/api/v1/agent-plans/{created['id']}/messages/stream",
            json={
                "content": (
                    "Test API https://api.example.test/openapi.json "
                    "with token=secret-token and read-only checks."
                )
            },
            headers=headers,
        ) as response:
            status_code = response.status_code
            content_type = response.headers.get("content-type", "")
            text = "".join(response.iter_text())

    assert status_code == 200
    assert content_type.startswith("text/event-stream")
    events = _sse_events(text)
    event_names = [event_name for event_name, _ in events]
    process_codes = [data["code"] for event_name, data in events if event_name == "process"]
    final_events = [data for event_name, data in events if event_name == "final"]
    assistant_text = "".join(
        data["delta"] for event_name, data in events if event_name == "token"
    )

    assert "process" in event_names
    assert "token" in event_names
    assert "final" in event_names
    assert process_codes[:4] == [
        "analyzing_requirement",
        "checking_missing_info",
        "normalizing_target",
        "preparing_plan",
    ]
    assert "waiting_for_confirmation" in process_codes
    assert "信息已足够" in assistant_text
    assert final_events[0]["session"]["status"] == "ready"
    assert final_events[0]["session"]["current_run_payload"]["token"] == "[REDACTED]"
    assert final_events[0]["session"]["question_options"] == []
    assert "secret-token" not in text


def test_planning_session_isolated_by_user(monkeypatch) -> None:
    async def fake_llm(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("planner unavailable")

    monkeypatch.setattr(agent_planning_service, "_call_planner_llm", fake_llm)

    other_username = f"plan-user-{uuid.uuid4().hex}"
    other_password = "other-password"
    with TestClient(app) as client:
        asyncio.run(_create_test_user(other_username, other_password))
        admin_token = _token(client)
        other_token = _login_token(client, other_username, other_password)
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        other_headers = {"Authorization": f"Bearer {other_token}"}

        created = client.post("/api/v1/agent-plans", json={}, headers=admin_headers).json()
        ready = client.post(
            f"/api/v1/agent-plans/{created['id']}/messages",
            json={"content": "Test API https://api.example.test/openapi.json."},
            headers=admin_headers,
        )
        assert ready.status_code == 200
        first_user_id = ready.json()["messages"][0]["id"]

        listed = client.get("/api/v1/agent-plans", headers=other_headers)
        get_response = client.get(f"/api/v1/agent-plans/{created['id']}", headers=other_headers)
        message_response = client.post(
            f"/api/v1/agent-plans/{created['id']}/messages",
            json={"content": "change it"},
            headers=other_headers,
        )
        edit_response = client.put(
            f"/api/v1/agent-plans/{created['id']}/messages/{first_user_id}",
            json={"content": "change it"},
            headers=other_headers,
        )
        stream_edit_response = client.put(
            f"/api/v1/agent-plans/{created['id']}/messages/{first_user_id}/stream",
            json={"content": "change it"},
            headers=other_headers,
        )
        delete_message_response = client.delete(
            f"/api/v1/agent-plans/{created['id']}/messages/{first_user_id}",
            headers=other_headers,
        )
        reject_response = client.post(
            f"/api/v1/agent-plans/{created['id']}/reject",
            json={"reason": "not mine"},
            headers=other_headers,
        )
        execute_response = client.post(
            f"/api/v1/agent-plans/{created['id']}/execute",
            headers=other_headers,
        )

    assert listed.status_code == 200
    assert all(item["id"] != created["id"] for item in listed.json())
    assert get_response.status_code == 404
    assert message_response.status_code == 404
    assert edit_response.status_code == 404
    assert stream_edit_response.status_code == 404
    assert delete_message_response.status_code == 404
    assert reject_response.status_code == 404
    assert execute_response.status_code == 404


# -------------------------------------------------------------------------
# T1: planner intelligence regressions
# `.trellis/tasks/05-28-agent-plan-planner-intelligence`
# -------------------------------------------------------------------------


def test_collecting_recognizes_provided_target_and_success_criteria(monkeypatch) -> None:
    """A user message that already names target + success criteria must not
    bounce back the same '请补充成功标准' question. Live audit bug 1.11."""

    async def fake_llm(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("planner unavailable")

    monkeypatch.setattr(agent_planning_service, "_call_planner_llm", fake_llm)

    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post("/api/v1/agent-plans", json={}, headers=headers).json()
        response = client.post(
            f"/api/v1/agent-plans/{created['id']}/messages",
            json={"content": "请测试 https://httpbin.org/get，状态码 200"},
            headers=headers,
        )

    assert response.status_code == 200
    body = response.json()
    # httpbin.org is a recognized public domain; status should reach ready.
    assert body["status"] == "ready"
    assert body["current_run_payload"]["auth_mode"] == "none_confirmed"
    # No clarifying question must be asked about success criteria when the
    # user already supplied "状态码 200".
    assistant_questions: list[str] = []
    for message in body["messages"]:
        if message["role"] != "assistant":
            continue
        plan_data = message.get("plan") or {}
        questions = plan_data.get("questions") if isinstance(plan_data, dict) else None
        if isinstance(questions, list):
            assistant_questions.extend(str(item) for item in questions if item)
    assert all("成功标准是什么" not in question for question in assistant_questions), assistant_questions


def test_collecting_recognizes_public_domain_auth_boundary(monkeypatch) -> None:
    """A public no-auth domain should not trigger a re-ask for the auth boundary."""

    async def fake_llm(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("planner unavailable")

    monkeypatch.setattr(agent_planning_service, "_call_planner_llm", fake_llm)

    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post("/api/v1/agent-plans", json={}, headers=headers).json()
        response = client.post(
            f"/api/v1/agent-plans/{created['id']}/messages",
            json={"content": "请测试 https://postman-echo.com/get"},
            headers=headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["current_run_payload"]["auth_mode"] == "none_confirmed"
    assistant_text = " ".join(
        message["content"] for message in body["messages"] if message["role"] == "assistant"
    )
    assert "是否需要鉴权" not in assistant_text


def test_repetition_guard_swaps_body_after_identical_generic_response(monkeypatch) -> None:
    """Second unparseable user input must not produce the same generic
    "还需要补充这些信息" reply. Live audit bug 1.2."""

    async def fake_llm(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("planner unavailable")

    monkeypatch.setattr(agent_planning_service, "_call_planner_llm", fake_llm)

    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post("/api/v1/agent-plans", json={}, headers=headers).json()
        first = client.post(
            f"/api/v1/agent-plans/{created['id']}/messages",
            json={"content": "测试一下百度的网页"},
            headers=headers,
        )
        second = client.post(
            f"/api/v1/agent-plans/{created['id']}/messages",
            json={"content": "1112"},
            headers=headers,
        )

    assert first.status_code == 200
    assert second.status_code == 200
    first_assistant = [m for m in first.json()["messages"] if m["role"] == "assistant"][-1]
    second_assistant = [m for m in second.json()["messages"] if m["role"] == "assistant"][-1]
    assert "还需要补充这些信息" in first_assistant["content"]
    # Second turn must not duplicate the first generic body.
    assert "上一轮的补充信息还没识别到" in second_assistant["content"]
    assert "还需要补充这些信息" not in second_assistant["content"]
    # question_options must remain populated so the frontend can guide the user.
    assert second.json()["question_options"], "guard must keep question_options"


def test_task_objective_dedupes_repeated_sentences(monkeypatch) -> None:
    """Asset handoff + user free chat must not duplicate the safety sentence.
    Live audit bug 1.4."""

    async def fake_llm(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("planner unavailable")

    monkeypatch.setattr(agent_planning_service, "_call_planner_llm", fake_llm)

    # The asset handoff message and a user follow-up that both contain the
    # safety boundary sentence ("安全边界：默认只读；...") must collapse to once.
    handoff = (
        "从 TestClaw 接口文档创建新测试计划。\n"
        "资产：ruoyi_wms\n"
        "Source URL：https://api.internal.test/openapi.json\n"
        "安全边界：默认只读；不要复用凭证、Token、Cookie、会话或验证码值。\n"
        "从 TestClaw 接口文档资产创建新测试计划。"
    )
    followup = "安全边界：默认只读；不要复用凭证、Token、Cookie、会话或验证码值。"

    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post("/api/v1/agent-plans", json={}, headers=headers).json()
        first = client.post(
            f"/api/v1/agent-plans/{created['id']}/messages",
            json={"content": handoff},
            headers=headers,
        )
        second = client.post(
            f"/api/v1/agent-plans/{created['id']}/messages",
            json={"content": followup},
            headers=headers,
        )

    assert first.status_code == 200
    assert second.status_code == 200
    # Even before becoming ready, the planner-derived objective text used as
    # task_objective surfaces in setup_instructions / objective when the
    # run_payload is composed. Verify by normalizing directly.
    from app.models.agent_planning import AgentPlanningMessage as _Msg

    composed = normalize_planner_run_payload(
        None,
        [
            _Msg(session_id="test-session", role="user", content=handoff),
            _Msg(session_id="test-session", role="user", content=followup),
        ],
    )
    safety_sentence = "安全边界：默认只读"
    assert composed.objective.count(safety_sentence) <= 1, composed.objective
    handoff_sentence = "从 TestClaw 接口文档创建新测试计划"
    assert composed.objective.count(handoff_sentence) <= 1, composed.objective


def test_free_chat_while_pending_step_does_not_silently_skip(monkeypatch) -> None:
    """Free-chat messages while a structured step is `待确认` must not silently
    advance the stepper. Live audit bug 1.13."""

    async def fake_llm(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("planner unavailable")

    monkeypatch.setattr(agent_planning_service, "_call_planner_llm", fake_llm)

    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post(
            "/api/v1/agent-plans/sessions",
            json={},
            headers=headers,
        ).json()
        session_id = created["id"]
        # Step 1: confirm target_kind via structured intake.
        client.post(
            f"/api/v1/agent-plans/sessions/{session_id}/intake",
            json={
                "action": "continue",
                "current_step": "target_kind",
                "selected_option": {
                    "label": "API / 接口",
                    "value": "api_openapi",
                    "field": "target_kind",
                    "step": "target_kind",
                    "message": "测试目标类型：API / OpenAPI/Swagger 接口来源。",
                },
            },
            headers=headers,
        )
        # User now opens the bottom chat composer and sends free-chat text
        # while coverage_scope is still 待确认.
        chat_response = client.post(
            f"/api/v1/agent-plans/{session_id}/messages",
            json={"content": "另外补充一些上下文"},
            headers=headers,
        )

    assert chat_response.status_code == 200
    body = chat_response.json()
    # Server must include current_step in every session payload so the
    # frontend stepper is driven from server state, not chat turn count.
    assert "current_step" in body
    # Status is still collecting; current_step must be a known intake stage,
    # never a silent jump that leaves coverage_scope at 待确认 while moving on.
    assert body["status"] == "collecting"
    assert body["current_step"] == "scope"


def test_chinese_credential_chat_message_is_persisted_redacted(monkeypatch) -> None:
    """Live audit bug 1.5: 用户消息 '登录账号是admin，密码是admin123' must not
    persist verbatim. Redacted form must appear in stored chat history and in
    any task_objective composition path."""

    async def fake_llm(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("planner unavailable")

    monkeypatch.setattr(agent_planning_service, "_call_planner_llm", fake_llm)

    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post("/api/v1/agent-plans", json={}, headers=headers).json()
        response = client.post(
            f"/api/v1/agent-plans/{created['id']}/messages",
            json={"content": "目标 https://api.internal.test/openapi.json 登录账号是admin，密码是admin123"},
            headers=headers,
        )

    assert response.status_code == 200
    body = response.json()
    user_message = next(m for m in body["messages"] if m["role"] == "user")
    assert "admin123" not in user_message["content"]
    assert "[REDACTED]" in user_message["content"]
    # Defensive: response body never echoes the secret.
    assert "admin123" not in response.text

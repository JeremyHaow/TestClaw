import asyncio
import json
import uuid
from datetime import datetime
from typing import Any

from fastapi.testclient import TestClient

from app.api.v1 import agent_plans
from app.core.security import hash_password
from app.database import AsyncSessionLocal
from app.main import app
from app.models.agent_planning import AgentPlanningMessage
from app.models.task import Task, TaskStatus, TestType as TaskTestType
from app.models.user import User
from app.services.agent_planning import agent_planning_service, normalize_planner_run_payload


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


def _message(content: str) -> AgentPlanningMessage:
    return AgentPlanningMessage(session_id="test-session", role="user", content=content)


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
            json={"content": "测试页面 https://example.com ，只做 UI 只读冒烟检查。"},
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
            json={"content": "测试页面 https://example.com ，只做 UI 只读冒烟检查。"},
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
    assert body["current_run_payload"]["source"] == "https://example.com"
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
            json={"content": "Use https://api.example.test/openapi.json for API read-only checks."},
            headers=headers,
        )

    assert regenerated.status_code == 200
    body = regenerated.json()
    assert body["status"] == "ready"
    assert body["current_run_payload"]["test_type"] == "api"
    assert body["current_run_payload"]["source"] == "https://api.example.test/openapi.json"


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
            json={"content": "Test API https://api.example.test/openapi.json."},
            headers=headers,
        )
        assert ready.status_code == 200
        response = client.post(
            f"/api/v1/agent-plans/{created['id']}/execute",
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
    assert rejected_after_execute.status_code == 400
    assert rejected_after_execute.json()["detail"] == "Executed plan cannot be rejected"


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

        listed = client.get("/api/v1/agent-plans", headers=other_headers)
        get_response = client.get(f"/api/v1/agent-plans/{created['id']}", headers=other_headers)
        message_response = client.post(
            f"/api/v1/agent-plans/{created['id']}/messages",
            json={"content": "change it"},
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
    assert reject_response.status_code == 404
    assert execute_response.status_code == 404

import asyncio
import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient

from app.api.v1 import runs
from app.database import AsyncSessionLocal
from app.main import app
from app.models.task import Task, TaskStatus, TestType as TaskTestType
from app.models.test_case import TestSuite as ModelTestSuite


def _token(client: TestClient) -> str:
    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "testclaw123"})
    assert login.status_code == 200
    return login.json()["access_token"]


def _openapi_for(host: str, path: str = "/health") -> dict[str, Any]:
    return {
        "openapi": "3.0.0",
        "info": {"title": "Memory API", "version": "1.0.0"},
        "servers": [{"url": f"https://{host}"}],
        "paths": {
            path: {
                "get": {
                    "summary": "Memory check",
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }


def _api_failure_log(host: str, title: str, secret: str) -> dict[str, Any]:
    return {
        "setup_instructions": f"Use demo account with password={secret}",
        "auth_config": {
            "enabled": True,
            "password": secret,
            "headers": {"Authorization": f"Bearer {secret}"},
        },
        "api_execution_result": {
            "total": 1,
            "executed": 1,
            "passed": 0,
            "failed": 1,
            "results": [
                {
                    "label": title,
                    "method": "GET",
                    "url": f"https://{host}/checkout?debug={secret}",
                    "status_code": 500,
                    "passed": False,
                    "failure_reason": f"Authorization: Bearer {secret}",
                    "request_headers": {"Authorization": f"Bearer {secret}"},
                }
            ],
        },
        "final_report": {
            "overall_verdict": "FAIL",
            "summary": f"Checkout failed with token={secret}",
            "bugs_found": [
                {
                    "title": title,
                    "severity": "HIGH",
                    "description": f"Checkout failed; password={secret}",
                    "source": "api",
                }
            ],
            "recommendations": [f"Fix checkout; api_key={secret}"],
        },
    }


async def _insert_task(
    *,
    host: str,
    status: TaskStatus,
    execution_log: dict[str, Any],
    created_at: datetime,
    path: str = "/checkout",
    test_type: TaskTestType = TaskTestType.API,
) -> str:
    task_id = str(uuid.uuid4())
    async with AsyncSessionLocal() as session:
        task = Task(
            id=task_id,
            objective="target memory fixture",
            target_url=f"https://{host}{path}?debug=target-query-secret",
            status=status,
            test_type=test_type,
            execution_log=json.dumps(execution_log),
            created_at=created_at,
        )
        session.add(task)
        await session.commit()
    return task_id


async def _insert_suite(task_id: str, name: str, case_ids: list[str]) -> None:
    async with AsyncSessionLocal() as session:
        session.add(ModelTestSuite(name=name, test_case_ids=case_ids, task_id=task_id))
        await session.commit()


def _patch_worker_ready(monkeypatch) -> None:
    async def fake_worker_readiness() -> tuple[str, str, str | None]:
        return "ready", "检测到 1 个活跃 Worker", None

    monkeypatch.setattr(runs, "_best_effort_worker_readiness", fake_worker_readiness)


def test_run_preflight_target_memory_appears_for_repeated_target_history(monkeypatch) -> None:
    _patch_worker_ready(monkeypatch)
    host = "repeat-memory.example.test"
    now = datetime.utcnow()
    repeated_title = "Checkout API returns 500 on GET /checkout"

    with TestClient(app) as client:
        token = _token(client)
        first_failure_id = asyncio.run(
            _insert_task(
                host=host,
                status=TaskStatus.FAILED,
                execution_log=_api_failure_log(host, repeated_title, "repeat-secret-one"),
                created_at=now - timedelta(days=3),
            )
        )
        asyncio.run(
            _insert_task(
                host=host,
                status=TaskStatus.SUCCEEDED,
                execution_log={
                    "api_execution_result": {"total": 1, "executed": 1, "passed": 1, "failed": 0},
                    "final_report": {"overall_verdict": "PASS", "summary": "All checks passed.", "bugs_found": []},
                },
                created_at=now - timedelta(days=2),
                path="/health",
            )
        )
        asyncio.run(
            _insert_task(
                host=host,
                status=TaskStatus.BUG_FOUND,
                execution_log=_api_failure_log(host, repeated_title, "repeat-secret-two"),
                created_at=now - timedelta(days=1),
            )
        )
        asyncio.run(_insert_suite(first_failure_id, "Checkout regression memory suite", ["case-a", "case-b"]))
        response = client.post(
            "/api/v1/runs/preflight",
            json={"source": json.dumps(_openapi_for(host, "/checkout")), "test_type": "api"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    memory = response.json()["target_memory"]
    assert memory["previous_run_count"] == 3
    assert memory["host_run_count"] == 3
    assert memory["last_run"]["status"] == "bug_found"
    assert memory["recurring_failure_themes"][0]["count"] == 2
    assert memory["recurring_failure_themes"][0]["category"] == "api"
    assert memory["reusable_suite_count"] == 1
    assert memory["reusable_case_count"] == 2
    assert memory["confidence"] == "high"
    assert memory["suggested_strategy"]


def test_run_preflight_target_memory_summarizes_auth_setup_blockers_safely(monkeypatch) -> None:
    _patch_worker_ready(monkeypatch)
    host = "auth-memory.example.test"
    with TestClient(app) as client:
        token = _token(client)
        asyncio.run(
            _insert_task(
                host=host,
                status=TaskStatus.FAILED,
                created_at=datetime.utcnow(),
                path="/login",
                execution_log={
                    "setup_result": {
                        "required": True,
                        "success": False,
                        "reason": "Login failed with password=setup-secret and captcha=captcha-secret",
                    },
                    "login_verified": False,
                    "api_execution_result": {
                        "total": 1,
                        "executed": 1,
                        "passed": 0,
                        "failed": 1,
                        "results": [
                            {
                                "label": "Private profile",
                                "method": "GET",
                                "url": f"https://{host}/private?next=plain-query-secret",
                                "status_code": 401,
                                "passed": False,
                                "failure_reason": "Authorization: Bearer auth-secret",
                                "request_headers": {"Authorization": "Bearer request-secret"},
                            }
                        ],
                    },
                    "final_report": {
                        "overall_verdict": "FAIL",
                        "summary": "Private profile requires login.",
                        "bugs_found": [],
                    },
                },
            )
        )
        response = client.post(
            "/api/v1/runs/preflight",
            json={"source": json.dumps(_openapi_for(host, "/private")), "test_type": "api"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    memory = response.json()["target_memory"]
    categories = {blocker["category"] for blocker in memory["known_blockers"]}
    serialized = json.dumps(memory, ensure_ascii=False)
    assert {"setup_auth", "api_auth"}.issubset(categories)
    assert "setup-secret" not in serialized
    assert "captcha-secret" not in serialized
    assert "auth-secret" not in serialized
    assert "request-secret" not in serialized
    assert "plain-query-secret" not in serialized
    assert "target-query-secret" not in serialized


def test_run_preflight_target_memory_does_not_leak_secret_bearing_history_fields(monkeypatch) -> None:
    _patch_worker_ready(monkeypatch)
    host = "leak-memory.example.test"
    with TestClient(app) as client:
        token = _token(client)
        asyncio.run(
            _insert_task(
                host=host,
                status=TaskStatus.FAILED,
                created_at=datetime.utcnow(),
                path="/private",
                execution_log={
                    "setup_instructions": "Fill login with password=setup-secret.",
                    "auth_config": {
                        "enabled": True,
                        "password": "auth-secret",
                        "headers": {"Authorization": "Bearer auth-header-secret"},
                    },
                    "setup_result": {
                        "required": True,
                        "success": False,
                        "reason": (
                            "Login blocked after fill \"#email\" \"generic-fill-secret\" "
                            "and type \"#password\" \"typed-secret\" then submit"
                        ),
                    },
                    "api_execution_result": {
                        "results": [
                            {
                                "label": "Private data",
                                "method": "GET",
                                "url": f"https://{host}/private?debug=debug-query-secret",
                                "status_code": 403,
                                "passed": False,
                                "failure_reason": "Cookie session=raw-cookie-secret",
                                "request_headers": {"X-Api-Key": "api-key-secret"},
                                "request_body": {"password": "body-secret"},
                            }
                        ],
                    },
                    "ui_execution_result": {
                        "cases": [
                            {
                                "title": "Login path",
                                "status": "failed",
                                "error": "fill \"Password\" \"ui-fill-secret\"",
                            }
                        ],
                    },
                    "final_report": {
                        "overall_verdict": "FAIL",
                        "summary": "Auth failed with token=report-secret.",
                        "recommendations": ["Rotate api_key=rec-secret"],
                        "bugs_found": [
                            {
                                "title": f"Private URL leaked https://{host}/private?debug=bug-query-secret",
                                "severity": "HIGH",
                                "description": "Unauthorized with password=bug-secret",
                                "source": "api",
                            }
                        ],
                    },
                },
            )
        )
        response = client.post(
            "/api/v1/runs/preflight",
            json={"source": json.dumps(_openapi_for(host, "/private")), "test_type": "api"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    serialized = json.dumps(response.json()["target_memory"], ensure_ascii=False)
    for secret in (
        "setup-secret",
        "auth-secret",
        "auth-header-secret",
        "generic-fill-secret",
        "typed-secret",
        "debug-query-secret",
        "raw-cookie-secret",
        "api-key-secret",
        "body-secret",
        "ui-fill-secret",
        "report-secret",
        "rec-secret",
        "bug-query-secret",
        "bug-secret",
        "target-query-secret",
    ):
        assert secret not in serialized


def test_run_preflight_target_memory_is_low_confidence_for_new_target(monkeypatch) -> None:
    _patch_worker_ready(monkeypatch)
    host = "fresh-memory.example.test"

    with TestClient(app) as client:
        token = _token(client)
        response = client.post(
            "/api/v1/runs/preflight",
            json={"source": json.dumps(_openapi_for(host)), "test_type": "api"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    memory = response.json()["target_memory"]
    assert memory["previous_run_count"] == 0
    assert memory["last_run"] is None
    assert memory["recurring_failure_themes"] == []
    assert memory["known_blockers"] == []
    assert memory["reusable_suite_count"] == 0
    assert memory["reusable_case_count"] == 0
    assert memory["confidence"] == "low"
    assert memory["suggested_strategy"]

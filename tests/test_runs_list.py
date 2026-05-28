import asyncio
import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.database import AsyncSessionLocal
from app.main import app
from app.models.task import Task, TaskStatus, TestType as TaskTestType


def _token(client: TestClient) -> str:
    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "testclaw123"})
    assert login.status_code == 200
    return login.json()["access_token"]


async def _replace_tasks(tasks: list[dict[str, Any]]) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(delete(Task))
        for task_data in tasks:
            session.add(
                Task(
                    id=task_data["id"],
                    objective=task_data["objective"],
                    target_url=task_data["target_url"],
                    status=task_data["status"],
                    test_type=task_data["test_type"],
                    generated_code=task_data.get("generated_code"),
                    execution_log=json.dumps(task_data.get("execution_log", {})),
                    created_at=task_data["created_at"],
                    updated_at=task_data["updated_at"],
                )
            )
        await session.commit()


def test_runs_list_uses_lightweight_payload_with_filters_and_pagination() -> None:
    now = datetime.utcnow()
    newest_failed_id = str(uuid.uuid4())
    older_failed_id = str(uuid.uuid4())
    successful_ui_id = str(uuid.uuid4())
    heavy_secret = "list-heavy-secret"
    heavy_log = {
        "last_error": f"Authorization: Bearer {heavy_secret}",
        "progress_events": [
            {"node": "api_runner", "status": "running", "detail": f"detail-{index}-{heavy_secret}"}
            for index in range(200)
        ],
    }
    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        asyncio.run(
            _replace_tasks(
                [
                    {
                        "id": newest_failed_id,
                        "objective": "new failed api",
                        "target_url": "https://api.example.test/new",
                        "status": TaskStatus.FAILED,
                        "test_type": TaskTestType.API,
                        "generated_code": f"generated-code-{heavy_secret}",
                        "execution_log": heavy_log,
                        "created_at": now,
                        "updated_at": now + timedelta(seconds=10),
                    },
                    {
                        "id": older_failed_id,
                        "objective": "older failed api",
                        "target_url": "https://api.example.test/older",
                        "status": TaskStatus.FAILED,
                        "test_type": TaskTestType.API,
                        "execution_log": heavy_log,
                        "created_at": now - timedelta(minutes=10),
                        "updated_at": now - timedelta(minutes=9),
                    },
                    {
                        "id": successful_ui_id,
                        "objective": "successful ui",
                        "target_url": "https://app.example.test",
                        "status": TaskStatus.SUCCEEDED,
                        "test_type": TaskTestType.UI,
                        "execution_log": heavy_log,
                        "created_at": now - timedelta(minutes=5),
                        "updated_at": now - timedelta(minutes=4),
                    },
                ]
            )
        )

        response = client.get(
            "/api/v1/runs?page=1&page_size=1&status=failed&test_type=api",
            headers=headers,
        )
        assert response.status_code == 200
        assert response.headers["x-total-count"] == "2"
        body = response.json()
        assert len(body) == 1
        assert body[0]["id"] == newest_failed_id
        assert body[0]["status"] == "failed"
        assert body[0]["test_type"] == "API"
        assert body[0]["updated_at"]
        assert "error_message" in body[0]
        assert "execution_log" not in body[0]
        assert "generated_code" not in body[0]
        assert "api_doc_id" not in body[0]
        assert "environment_id" not in body[0]
        assert heavy_secret not in response.text

        next_page = client.get(
            "/api/v1/runs?page=2&page_size=1&status=failed&test_type=api",
            headers=headers,
        )
        assert next_page.status_code == 200
        assert next_page.headers["x-total-count"] == "2"
        assert next_page.json()[0]["id"] == older_failed_id

        search_response = client.get(
            "/api/v1/runs",
            params={"page": 1, "page_size": 10, "search": "older"},
            headers=headers,
        )
        assert search_response.status_code == 200
        assert search_response.headers["x-total-count"] == "1"
        assert search_response.json()[0]["id"] == older_failed_id

        window_response = client.get(
            "/api/v1/runs",
            params={
                "page": 1,
                "page_size": 10,
                "created_after": (now - timedelta(minutes=6)).isoformat(),
            },
            headers=headers,
        )
        assert window_response.status_code == 200
        assert window_response.headers["x-total-count"] == "2"
        assert {item["id"] for item in window_response.json()} == {newest_failed_id, successful_ui_id}

        before_response = client.get(
            "/api/v1/runs",
            params={
                "page": 1,
                "page_size": 10,
                "created_before": (now - timedelta(minutes=6)).isoformat(),
            },
            headers=headers,
        )
        assert before_response.status_code == 200
        assert before_response.headers["x-total-count"] == "1"
        assert before_response.json()[0]["id"] == older_failed_id

        detail = client.get(f"/api/v1/runs/{newest_failed_id}", headers=headers)
        assert detail.status_code == 200
        assert "execution_log" in detail.json()


def test_runs_list_exposes_total_count_header_to_browser_clients() -> None:
    """History pagination depends on Axios being able to read X-Total-Count."""

    with TestClient(app) as client:
        token = _token(client)
        response = client.get(
            "/api/v1/runs?page=1&page_size=1",
            headers={
                "Authorization": f"Bearer {token}",
                "Origin": "http://127.0.0.1:5173",
            },
        )

        assert response.status_code == 200
        assert "x-total-count" in response.headers
        exposed = response.headers.get("access-control-expose-headers", "")
        assert "x-total-count" in exposed.lower()


def test_runs_list_includes_lightweight_triage_counts_without_raw_log() -> None:
    now = datetime.utcnow()
    run_id = str(uuid.uuid4())
    heavy_secret = "list-triage-secret"
    execution_log = {
        "last_error": f"Authorization: Bearer {heavy_secret}",
        "api_execution_result": {
            "total": 1,
            "executed": 1,
            "passed": 1,
            "failed": 0,
            "results": [
                {
                    "label": "GET /health",
                    "method": "GET",
                    "url": "https://api.example.test/health",
                    "status_code": 200,
                    "passed": True,
                }
            ],
        },
        "tool_summary": {"total": 2},
        "final_report": {
            "overall_verdict": "PASS",
            "summary": "Health endpoint passed with reusable API evidence.",
            "bugs_found": [],
        },
    }
    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        asyncio.run(
            _replace_tasks(
                [
                    {
                        "id": run_id,
                        "objective": "successful api with evidence",
                        "target_url": "https://api.example.test",
                        "status": TaskStatus.SUCCEEDED,
                        "test_type": TaskTestType.API,
                        "execution_log": execution_log,
                        "created_at": now,
                        "updated_at": now + timedelta(seconds=5),
                    }
                ]
            )
        )

        response = client.get("/api/v1/runs?page=1&page_size=5", headers=headers)

        assert response.status_code == 200
        body = response.json()
        assert body[0]["id"] == run_id
        assert body[0]["evidence_count"] == 3
        assert body[0]["issue_count"] == 0
        assert body[0]["triage_summary"]["evidence"]["api_result_count"] == 1
        assert body[0]["triage_summary"]["release_risk"]["level"] == "low"
        assert "execution_log" not in body[0]
        assert heavy_secret not in response.text


def test_runs_list_and_detail_derive_terminal_status_from_completed_log() -> None:
    now = datetime.utcnow()
    run_id = str(uuid.uuid4())
    execution_log = {
        "api_execution_result": {
            "total": 2,
            "completed": 2,
            "passed": 1,
            "failed": 1,
            "all_passed": False,
            "results": [
                {"label": "GET /health", "status_code": 200, "passed": True},
                {
                    "label": "GET /profile without auth",
                    "status_code": 401,
                    "passed": False,
                    "failure_type": "auth_failure",
                },
            ],
        },
        "workflow_steps": [
            {"node": "api_runner", "status": "failed", "detail": "1 failed"},
            {"node": "reporter", "status": "done", "detail": "Report generated"},
            {"node": "knowledge_sink", "status": "done", "detail": "Memory candidate generated"},
        ],
        "current_step": {
            "node": "knowledge_sink",
            "status": "done",
            "detail": "Memory candidate generated",
        },
        "final_report": {
            "overall_verdict": "PARTIAL",
            "summary": "One protected endpoint failed as expected before auth handling was fixed.",
            "bugs_found": [
                {
                    "title": "Protected endpoint negative case was treated as a failure",
                    "source": "api",
                    "severity": "HIGH",
                }
            ],
        },
    }
    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        asyncio.run(
            _replace_tasks(
                [
                    {
                        "id": run_id,
                        "objective": "stale running task with terminal log",
                        "target_url": "https://api.example.test",
                        "status": TaskStatus.RUNNING,
                        "test_type": TaskTestType.API,
                        "execution_log": execution_log,
                        "created_at": now,
                        "updated_at": now + timedelta(seconds=5),
                    }
                ]
            )
        )

        list_response = client.get("/api/v1/runs?page=1&page_size=5", headers=headers)
        detail_response = client.get(f"/api/v1/runs/{run_id}", headers=headers)

        assert list_response.status_code == 200
        assert list_response.json()[0]["status"] == "bug_found"
        assert list_response.json()[0]["issue_count"] == 1
        assert detail_response.status_code == 200
        assert detail_response.json()["status"] == "bug_found"


def test_runs_list_preserves_filter_validation() -> None:
    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        status_response = client.get(
            "/api/v1/runs?status=not-a-status",
            headers=headers,
        )
        assert status_response.status_code == 400
        assert "Unsupported status" in status_response.json()["detail"]

        type_response = client.get(
            "/api/v1/runs?test_type=not-a-type",
            headers=headers,
        )
        assert type_response.status_code == 400
        assert "Unsupported test_type" in type_response.json()["detail"]


def test_runs_list_returns_empty_page_with_total_zero() -> None:
    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        asyncio.run(_replace_tasks([]))

        response = client.get(
            "/api/v1/runs?status=queued&test_type=suite",
            headers=headers,
        )
        assert response.status_code == 200
        assert response.headers["x-total-count"] == "0"
        assert response.json() == []

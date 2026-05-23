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
                        "id": str(uuid.uuid4()),
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

        detail = client.get(f"/api/v1/runs/{newest_failed_id}", headers=headers)
        assert detail.status_code == 200
        assert "execution_log" in detail.json()


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

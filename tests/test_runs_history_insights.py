import asyncio
import json
import uuid
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.database import AsyncSessionLocal
from app.main import app
from app.models.task import Task, TaskStatus, TestType as TaskTestType


def _token(client: TestClient) -> str:
    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "testclaw123"})
    assert login.status_code == 200
    return login.json()["access_token"]


async def _replace_tasks(tasks: list[dict]) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(delete(Task))
        for task_data in tasks:
            task = Task(
                id=str(uuid.uuid4()),
                objective=task_data["objective"],
                target_url=task_data["target_url"],
                status=task_data["status"],
                test_type=task_data.get("test_type", TaskTestType.API),
                execution_log=json.dumps(task_data.get("execution_log", {})),
                created_at=task_data["created_at"],
            )
            session.add(task)
        await session.commit()


def _api_failure_log(title: str, secret: str) -> dict:
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
                    "url": f"https://api.example.test/checkout?token={secret}",
                    "status_code": 500,
                    "passed": False,
                    "failure_type": "backend_error",
                    "failure_reason": f"Authorization: Bearer {secret}",
                    "request_headers": {"Authorization": f"Bearer {secret}"},
                }
            ],
        },
        "final_report": {
            "overall_verdict": "FAIL",
            "summary": f"Checkout regression reproduced with token={secret}.",
            "bugs_found": [
                {
                    "title": title,
                    "severity": "HIGH",
                    "description": f"Checkout API returned 500; password={secret}",
                    "source": "api",
                }
            ],
            "recommendations": [f"Fix checkout before release; api_key={secret}"],
        },
        "artifacts": {"tool_calls": [{"tool": "api.request", "status": "failed"}]},
    }


def test_run_history_insights_summarizes_quality_memory_without_secrets() -> None:
    now = datetime.utcnow()
    repeated_title = "Checkout API returns 500 on GET https://api.example.test/checkout"
    with TestClient(app) as client:
        token = _token(client)
        asyncio.run(
            _replace_tasks(
                [
                    {
                        "objective": "health baseline",
                        "target_url": "https://api.example.test/health?token=target-secret",
                        "status": TaskStatus.SUCCEEDED,
                        "created_at": now - timedelta(days=6),
                        "execution_log": {
                            "api_execution_result": {
                                "total": 1,
                                "executed": 1,
                                "passed": 1,
                                "failed": 0,
                                "results": [
                                    {
                                        "label": "Health check",
                                        "method": "GET",
                                        "url": "https://api.example.test/health",
                                        "status_code": 200,
                                        "passed": True,
                                    }
                                ],
                            },
                            "final_report": {
                                "overall_verdict": "PASS",
                                "summary": "All checks passed.",
                                "bugs_found": [],
                                "recommendations": [],
                            },
                        },
                    },
                    {
                        "objective": "checkout regression one",
                        "target_url": "https://api.example.test/checkout?token=target-secret",
                        "status": TaskStatus.FAILED,
                        "created_at": now - timedelta(days=3),
                        "execution_log": _api_failure_log(repeated_title, "api-secret-one"),
                    },
                    {
                        "objective": "checkout regression two",
                        "target_url": "https://api.example.test/checkout?token=target-secret",
                        "status": TaskStatus.BUG_FOUND,
                        "created_at": now - timedelta(days=2),
                        "execution_log": _api_failure_log(repeated_title, "api-secret-two"),
                    },
                    {
                        "objective": "ui evidence failure",
                        "target_url": "https://web.example.test/login?password=target-secret",
                        "status": TaskStatus.FAILED,
                        "test_type": TaskTestType.UI,
                        "created_at": now - timedelta(days=1),
                        "execution_log": {
                            "ui_execution_result": {
                                "total": 1,
                                "completed": 1,
                                "passed": 0,
                                "failed": 1,
                                "cases": [
                                    {
                                        "title": "Checkout page submit button unavailable",
                                        "status": "failed",
                                        "screenshots": ["screenshots/run/case_000_step_001.png"],
                                    }
                                ],
                                "screenshots": ["screenshots/run/case_000_step_001.png"],
                            },
                            "final_report": {
                                "overall_verdict": "FAIL",
                                "summary": "UI checkout path failed.",
                                "bugs_found": [],
                                "recommendations": [],
                            },
                            "ui_reproducible_script": "test('checkout', async () => {})",
                        },
                    },
                ]
            )
        )
        response = client.get(
            "/api/v1/runs/insights?days=30&limit=20",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    body = response.json()
    serialized = json.dumps(body, ensure_ascii=False)

    assert body["window_run_count"] == 4
    assert body["analyzed_runs"] == 4
    assert body["status_counts"]["succeeded"] == 1
    assert body["status_counts"]["failed"] == 2
    assert body["status_counts"]["bug_found"] == 1
    assert body["status_counts"]["issue_rate"] == 75.0
    assert body["quality_trend"]["direction"] == "regressing"
    assert body["quality_trend"]["buckets"]
    assert body["affected_targets"][0]["issue_run_count"] >= 2
    assert any(surface["name"] == "GET /checkout" for surface in body["affected_surfaces"])
    assert body["recurring_themes"][0]["count"] == 2
    assert body["recurring_themes"][0]["category"] == "api"
    assert body["evidence_reproduction"]["runs_with_api_evidence"] >= 3
    assert body["evidence_reproduction"]["runs_with_reproduction"] >= 3
    assert body["evidence_reproduction"]["runs_with_scripts"] == 1
    assert body["recommended_next_actions"]

    for secret in (
        "target-secret",
        "api-secret-one",
        "api-secret-two",
        "Bearer api-secret",
    ):
        assert secret not in serialized


def test_run_history_insights_handles_empty_history() -> None:
    with TestClient(app) as client:
        token = _token(client)
        asyncio.run(_replace_tasks([]))
        response = client.get("/api/v1/runs/insights", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    assert body["analyzed_runs"] == 0
    assert body["status_counts"]["total"] == 0
    assert body["recurring_themes"] == []
    assert body["affected_targets"] == []
    assert body["recommended_next_actions"]


def test_run_history_insights_omits_stdout_stderr_dumps() -> None:
    now = datetime.utcnow()
    with TestClient(app) as client:
        token = _token(client)
        asyncio.run(
            _replace_tasks(
                [
                    {
                        "objective": "ui command failure",
                        "target_url": "https://web.example.test",
                        "status": TaskStatus.FAILED,
                        "test_type": TaskTestType.UI,
                        "created_at": now,
                        "execution_log": {
                            "ui_execution_result": {
                                "total": 1,
                                "completed": 1,
                                "passed": 0,
                                "failed": 1,
                                "commands": [
                                    {
                                        "command": "click submit",
                                        "status_code": -1,
                                        "passed": False,
                                        "stdout": "raw-stdout-dump-marker",
                                        "stderr": "raw-stderr-dump-marker password=stdio-secret",
                                    }
                                ],
                            },
                            "final_report": {
                                "overall_verdict": "FAIL",
                                "summary": "UI command failed.",
                                "bugs_found": [],
                                "recommendations": [],
                            },
                        },
                    }
                ]
            )
        )
        response = client.get("/api/v1/runs/insights", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    serialized = json.dumps(response.json(), ensure_ascii=False)
    assert "raw-stdout-dump-marker" not in serialized
    assert "raw-stderr-dump-marker" not in serialized
    assert "stdio-secret" not in serialized

import asyncio
import json
import uuid

from fastapi.testclient import TestClient

from app.api.v1 import runs
from app.core.redaction import REDACTED_VALUE
from app.database import AsyncSessionLocal
from app.main import app
from app.models.task import Task, TaskStatus, TestType as TaskTestType


def _token(client: TestClient) -> str:
    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "testclaw123"})
    assert login.status_code == 200
    return login.json()["access_token"]


async def _insert_task(
    *,
    status: TaskStatus,
    execution_log: dict,
    test_type: TaskTestType = TaskTestType.UI,
    target_url: str = "https://app.example.test/login",
) -> str:
    task_id = str(uuid.uuid4())
    async with AsyncSessionLocal() as session:
        task = Task(
            id=task_id,
            objective="intervention detail",
            target_url=target_url,
            status=status,
            test_type=test_type,
            execution_log=json.dumps(execution_log),
        )
        session.add(task)
        await session.commit()
    return task_id


async def _task_status(task_id: str) -> str:
    async with AsyncSessionLocal() as session:
        task = await session.get(Task, task_id)
        assert task is not None
        return task.status.value if hasattr(task.status, "value") else str(task.status)


def test_run_detail_intervention_summary_surfaces_setup_login_blocker_without_secrets() -> None:
    execution_log = {
        "last_error": "Pre-test setup verification failed: password=setup-secret, login form still visible",
        "setup_result": {
            "required": True,
            "success": False,
            "reason": "Login failed with token=login-secret",
        },
        "login_verified": False,
        "ui_execution_result": {"total": 0, "completed": 0, "passed": 0, "failed": 1},
    }

    with TestClient(app) as client:
        token = _token(client)
        task_id = asyncio.run(_insert_task(status=TaskStatus.FAILED, execution_log=execution_log))
        response = client.get(f"/api/v1/runs/{task_id}", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    summary = response.json()["intervention_summary"]
    serialized = json.dumps(summary, ensure_ascii=False)

    assert summary["useful"] is True
    assert summary["category"] == "setup_auth"
    assert summary["assisted_rerun_enabled"] is True
    assert any("测试账号" in item for item in summary["suggested_inputs"])
    assert "setup-secret" not in serialized
    assert "login-secret" not in serialized
    assert REDACTED_VALUE in serialized


def test_run_detail_intervention_summary_surfaces_api_auth_blocker() -> None:
    execution_log = {
        "api_execution_result": {
            "total": 1,
            "executed": 0,
            "passed": 0,
            "failed": 0,
            "skipped": 1,
            "results": [
                {
                    "method": "GET",
                    "url": "https://api.example.test/private",
                    "skipped": True,
                    "skip_reason": "Authorization token missing",
                }
            ],
        }
    }

    with TestClient(app) as client:
        token = _token(client)
        task_id = asyncio.run(
            _insert_task(
                status=TaskStatus.FAILED,
                execution_log=execution_log,
                test_type=TaskTestType.API,
                target_url="https://api.example.test",
            )
        )
        response = client.get(f"/api/v1/runs/{task_id}", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    summary = response.json()["intervention_summary"]
    assert summary["useful"] is True
    assert summary["category"] == "api_auth"
    assert "Token/Header" in summary["recommended_action"]


def test_assisted_intervention_rerun_appends_setup_instructions_and_redacts_response(monkeypatch) -> None:
    dispatched: dict = {}

    def fake_delay(task_id: str, objective: str, target_url: str, **kwargs) -> None:
        dispatched["task_id"] = task_id
        dispatched["objective"] = objective
        dispatched["target_url"] = target_url
        dispatched["kwargs"] = kwargs

    monkeypatch.setattr(runs.run_agent_task, "delay", fake_delay)

    execution_log = {
        "source_input": "https://app.example.test/login",
        "input_type": "url",
        "ui_seed_url": "https://app.example.test/login",
        "setup_instructions": "Use tenant qa before testing.",
        "auth_headers": {"Authorization": "Bearer old-secret", "X-Tenant": "qa"},
        "custom_headers": {
            "X-Trace-ID": "trace-safe",
            "X-Debug-Auth": f"Bearer {REDACTED_VALUE}",
            "X-Note": "password=stale-secret",
        },
    }
    supplemental = "Use username demo and password=rerun-secret; captcha=captcha-secret."

    with TestClient(app) as client:
        token = _token(client)
        task_id = asyncio.run(_insert_task(status=TaskStatus.FAILED, execution_log=execution_log))
        response = client.post(
            f"/api/v1/runs/{task_id}/interventions",
            json={"supplemental_instructions": supplemental},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        new_run_id = response.json()["id"]
        detail = client.get(f"/api/v1/runs/{new_run_id}", headers={"Authorization": f"Bearer {token}"})

    assert dispatched["target_url"] == "https://app.example.test/login"
    assert dispatched["kwargs"]["auth_headers"] == {"X-Tenant": "qa"}
    assert dispatched["kwargs"]["custom_headers"] == {"X-Trace-ID": "trace-safe"}
    assert "Use tenant qa" in dispatched["kwargs"]["setup_instructions"]
    assert "password=rerun-secret" in dispatched["kwargs"]["setup_instructions"]
    assert dispatched["kwargs"]["login_instructions"] == dispatched["kwargs"]["setup_instructions"]

    serialized_response = response.text + detail.text
    assert "rerun-secret" not in serialized_response
    assert "captcha-secret" not in serialized_response
    assert "old-secret" not in serialized_response
    assert REDACTED_VALUE in serialized_response


def test_assisted_intervention_rejects_active_run_without_cancel_flag(monkeypatch) -> None:
    def fail_delay(*args, **kwargs) -> None:
        raise AssertionError("active run without cancel_current must not dispatch")

    monkeypatch.setattr(runs.run_agent_task, "delay", fail_delay)

    with TestClient(app) as client:
        token = _token(client)
        task_id = asyncio.run(
            _insert_task(
                status=TaskStatus.RUNNING,
                execution_log={"last_error": "Waiting for login credentials"},
            )
        )
        response = client.post(
            f"/api/v1/runs/{task_id}/interventions",
            json={"supplemental_instructions": "password=active-secret"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 400
    assert "still active" in response.json()["detail"]


def test_assisted_intervention_can_cancel_active_run_before_rerun(monkeypatch) -> None:
    dispatched: dict = {}

    def fake_delay(task_id: str, objective: str, target_url: str, **kwargs) -> None:
        dispatched["task_id"] = task_id
        dispatched["kwargs"] = kwargs

    monkeypatch.setattr(runs.run_agent_task, "delay", fake_delay)
    monkeypatch.setattr(runs, "_revoke_worker_task", lambda run_id: None)

    with TestClient(app) as client:
        token = _token(client)
        task_id = asyncio.run(
            _insert_task(
                status=TaskStatus.RUNNING,
                execution_log={
                    "source_input": "https://app.example.test/login",
                    "input_type": "url",
                    "ui_seed_url": "https://app.example.test/login",
                    "last_error": "Waiting for login credentials",
                },
            )
        )
        response = client.post(
            f"/api/v1/runs/{task_id}/interventions",
            json={
                "supplemental_instructions": "Use test account password=active-secret.",
                "cancel_current": True,
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert asyncio.run(_task_status(task_id)) == TaskStatus.CANCELLED.value
    assert dispatched["task_id"] == response.json()["id"]
    assert "password=active-secret" in dispatched["kwargs"]["setup_instructions"]

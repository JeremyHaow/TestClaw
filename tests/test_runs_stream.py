import asyncio
import json
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.main import app
from app.models.run_event import RunEvent
from app.models.task import Task, TaskStatus, TestType as TaskTestType


def _token(client: TestClient) -> str:
    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "testclaw123"})
    assert login.status_code == 200
    return login.json()["access_token"]


async def _insert_task(*, status: TaskStatus, execution_log: dict) -> str:
    task_id = str(uuid.uuid4())
    async with AsyncSessionLocal() as session:
        task = Task(
            id=task_id,
            objective="stream run",
            target_url="https://api.example.test",
            status=status,
            test_type=TaskTestType.API,
            execution_log=json.dumps(execution_log),
        )
        session.add(task)
        await session.commit()
    return task_id


async def _load_run_events(run_id: str) -> list[RunEvent]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.sequence)
        )
        return list(result.scalars())


def _stream_body(client: TestClient, run_id: str, token: str) -> tuple[str, str]:
    with client.stream("GET", f"/api/v1/runs/{run_id}/stream?token={token}") as response:
        content_type = response.headers.get("content-type", "")
        body = "".join(response.iter_text())
    return content_type, body


def test_run_stream_returns_event_stream_and_terminal_done_payload() -> None:
    execution_log = {
        "workflow_steps": [{"node": "reporter", "status": "finished", "detail": "Report done"}],
        "final_report": {"overall_verdict": "PASS", "summary": "All checks passed."},
    }

    with TestClient(app) as client:
        token = _token(client)
        run_id = asyncio.run(_insert_task(status=TaskStatus.SUCCEEDED, execution_log=execution_log))
        content_type, body = _stream_body(client, run_id, token)

    assert content_type.startswith("text/event-stream")
    assert "event: run.finished" in body
    assert '"type": "done"' in body
    assert '"status": "succeeded"' in body
    assert '"type": "status"' in body
    assert '"type": "snapshot"' in body
    assert '"type": "workflow"' in body
    assert '"type": "log"' in body


def test_run_stream_redacts_execution_log_secrets() -> None:
    execution_log = {
        "workflow_steps": [
            {
                "node": "setup",
                "status": "failed",
                "detail": "Login failed with password=stream-secret",
            }
        ],
        "current_step": {
            "node": "api_runner",
            "status": "failed",
            "detail": "Authorization: Bearer step-secret",
        },
        "api_execution_result": {
            "results": [
                {
                    "request_headers": {"Authorization": "Bearer header-secret"},
                    "failure_reason": "token=result-secret",
                }
            ]
        },
        "final_report": {
            "overall_verdict": "FAIL",
            "summary": "Report contains password=report-secret",
        },
    }

    with TestClient(app) as client:
        token = _token(client)
        run_id = asyncio.run(_insert_task(status=TaskStatus.FAILED, execution_log=execution_log))
        _, body = _stream_body(client, run_id, token)

    for secret in ("stream-secret", "step-secret", "header-secret", "result-secret", "report-secret"):
        assert secret not in body


def test_run_stream_persists_redacted_run_events() -> None:
    execution_log = {
        "workflow_steps": [{"node": "reporter", "status": "finished", "detail": "token=row-secret"}],
        "final_report": {"overall_verdict": "FAIL", "summary": "password=row-report-secret"},
    }

    with TestClient(app) as client:
        token = _token(client)
        run_id = asyncio.run(_insert_task(status=TaskStatus.FAILED, execution_log=execution_log))
        _stream_body(client, run_id, token)

    events = asyncio.run(_load_run_events(run_id))
    event_types = {event.event_type for event in events}
    serialized = json.dumps(
        [event.payload_json for event in events],
        ensure_ascii=False,
        default=str,
    )

    assert {"run.status", "run.finished"} & event_types
    assert "row-secret" not in serialized
    assert "row-report-secret" not in serialized


def test_run_stream_returns_404_for_unknown_run() -> None:
    with TestClient(app) as client:
        token = _token(client)
        response = client.get(f"/api/v1/runs/{uuid.uuid4()}/stream?token={token}")

    assert response.status_code == 404
    assert response.json()["detail"] == "Run not found"

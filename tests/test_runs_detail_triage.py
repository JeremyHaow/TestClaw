import asyncio
import json
import uuid

from fastapi.testclient import TestClient

from app.api.v1 import runs as runs_api
from app.database import AsyncSessionLocal
from app.main import app
from app.models.task import Task, TaskStatus, TestType as TaskTestType
from app.models.test_case import TestSuite


def _token(client: TestClient) -> str:
    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "testclaw123"})
    assert login.status_code == 200
    return login.json()["access_token"]


async def _insert_task(
    *,
    status: TaskStatus,
    execution_log: dict,
    objective: str = "triage detail",
    target_url: str = "https://api.example.test",
) -> str:
    task_id = str(uuid.uuid4())
    async with AsyncSessionLocal() as session:
        task = Task(
            id=task_id,
            objective=objective,
            target_url=target_url,
            status=status,
            test_type=TaskTestType.API,
            execution_log=json.dumps(execution_log),
        )
        session.add(task)
        await session.commit()
    return task_id


async def _insert_suite(*, run_id: str, name: str = "Release smoke suite") -> str:
    suite_id = str(uuid.uuid4())
    async with AsyncSessionLocal() as session:
        suite = TestSuite(
            id=suite_id,
            name=name,
            test_case_ids=[str(uuid.uuid4()), str(uuid.uuid4())],
            task_id=run_id,
        )
        session.add(suite)
        await session.commit()
    return suite_id


def _triage_export_execution_log() -> dict:
    return {
        "setup_instructions": "Log in with demo user and password=setup-secret before testing.",
        "auth_config": {
            "enabled": True,
            "username": "demo",
            "password": "auth-secret",
            "headers": {
                "Authorization": "Bearer auth-header-secret",
                "Cookie": "session=cookie-secret",
                "X-CSRF": "csrf-secret",
            },
        },
        "supplemental_intervention": {
            "supplemental_instructions": "Use otp=intervention-secret for the rerun.",
        },
        "api_cases": [{"title": "Private data smoke"}],
        "ui_cases": [{"title": "Dashboard load"}],
        "test_cases": [{"title": "Legacy smoke"}],
        "api_execution_result": {
            "total": 1,
            "executed": 1,
            "passed": 0,
            "failed": 1,
            "skipped": 0,
            "results": [
                {
                    "label": "Private data smoke",
                    "method": "GET",
                    "url": "https://api.example.test/private?debug=query-secret&token=query-token-secret",
                    "status_code": "500 token=status-secret",
                    "passed": False,
                    "failure_type": "backend_error",
                    "failure_reason": (
                        "Server error returned with Authorization: Bearer api-secret; "
                        "request_body={\"note\":\"body-secret\"}; "
                        "JWT jwt-result-secret; CSRF csrf-result-secret"
                    ),
                    "request_headers": {
                        "Authorization": "Bearer request-secret",
                        "Cookie": "session=request-cookie-secret",
                    },
                    "request_body": {"note": "body-secret"},
                }
            ],
        },
        "ui_execution_result": {
            "total": 1,
            "completed": 1,
            "passed": 0,
            "failed": 1,
            "cases": [
                {
                    "title": "Login check",
                    "passed": False,
                    "failure_reason": 'Login failed after fill "#password" "fill-secret"',
                    "failed_commands": [
                        {
                            "command": 'type "#otp" "otp-command-secret"',
                            "stdout": "stdout-secret",
                            "stderr": "stderr-secret",
                        }
                    ],
                    "screenshots": [{"path": "screenshots/run/case_0.png"}],
                }
            ],
        },
        "final_report": {
            "overall_verdict": "FAIL",
            "summary": (
                "Release validation failed for "
                "https://api.example.test/private?debug=report-query-secret. "
                "Query params debug=debug-query-secret and page=plain-page-secret. "
                "JWT jwt-summary-secret; X-CSRF csrf-summary-secret; X-XSRF xsrf-summary-secret. "
                "request body={\"note\":\"summary-body-secret\"}."
            ),
            "api_test_summary": {"total": 1, "passed": 0, "failed": 1, "skipped": 0},
            "ui_test_summary": {"total": 1, "passed": 0, "failed": 1, "skipped": 0},
            "bugs_found": [
                {
                    "title": "API server error: GET /private?cart=cart-secret",
                    "severity": "CRITICAL",
                    "description": "Private data returned a server error; password=bug-secret",
                    "source": "api",
                }
            ],
            "recommendations": [
                "Fix backend before release; token=rec-secret",
                'Recheck login command type "#mfa" "mfa-secret"',
                "Do not copy Authorization Bearer recommendation-auth-secret or Cookie session=recommendation-cookie-secret",
            ],
        },
        "artifacts": {
            "tool_calls": [{"tool": "api.request", "status": "failed"}],
            "ui_reproducible_script": 'await page.fill("#password", "script-secret")',
        },
    }


def test_run_detail_triage_summary_surfaces_blocking_failure_without_secrets() -> None:
    execution_log = {
        "setup_instructions": "Log in with demo user and password=setup-secret before testing.",
        "auth_config": {
            "enabled": True,
            "username": "demo",
            "password": "auth-secret",
            "headers": {"Authorization": "Bearer auth-header-secret"},
        },
        "api_execution_result": {
            "total": 1,
            "executed": 1,
            "passed": 0,
            "failed": 1,
            "skipped": 0,
            "results": [
                {
                    "label": "Private data smoke",
                    "method": "GET",
                    "url": "https://api.example.test/private?token=query-secret",
                    "status_code": 500,
                    "passed": False,
                    "failure_type": "backend_error",
                    "failure_reason": "Server error returned with Authorization: Bearer api-secret",
                    "request_headers": {"Authorization": "Bearer request-secret"},
                }
            ],
        },
        "final_report": {
            "overall_verdict": "FAIL",
            "summary": "API failed during release validation.",
            "api_test_summary": {"total": 1, "passed": 0, "failed": 1, "skipped": 0},
            "ui_test_summary": {"total": 0, "passed": 0, "failed": 0, "skipped": 0},
            "bugs_found": [
                {
                    "title": "API server error: GET https://api.example.test/private",
                    "severity": "CRITICAL",
                    "description": "Private data returned a server error; password=bug-secret",
                    "source": "api",
                }
            ],
            "recommendations": ["Fix the backend error before release; token=rec-secret"],
        },
        "artifacts": {"tool_calls": [{"tool": "api.request", "status": "failed"}]},
    }

    with TestClient(app) as client:
        token = _token(client)
        task_id = asyncio.run(_insert_task(status=TaskStatus.BUG_FOUND, execution_log=execution_log))
        response = client.get(f"/api/v1/runs/{task_id}", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    triage = response.json()["triage_summary"]
    serialized = json.dumps(triage, ensure_ascii=False)

    assert triage["release_risk"]["level"] == "high"
    assert triage["blocking_count"] == 1
    assert triage["blocking_findings"][0]["severity"] == "CRITICAL"
    assert triage["evidence"]["api_result_count"] == 1
    assert triage["evidence"]["count"] >= 2
    assert any(surface["type"] == "api_endpoint" and surface["name"] == "GET /private" for surface in triage["affected_surfaces"])
    assert triage["reproduction"]["available"] is True
    for secret in ("setup-secret", "auth-secret", "auth-header-secret", "query-secret", "api-secret", "request-secret", "bug-secret", "rec-secret"):
        assert secret not in serialized


def test_run_detail_triage_summary_marks_passed_run_as_low_risk() -> None:
    execution_log = {
        "api_execution_result": {
            "total": 1,
            "executed": 1,
            "passed": 1,
            "failed": 0,
            "skipped": 0,
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
            "summary": "All executed checks passed.",
            "api_test_summary": {"total": 1, "passed": 1, "failed": 0, "skipped": 0},
            "ui_test_summary": {"total": 0, "passed": 0, "failed": 0, "skipped": 0},
            "bugs_found": [],
            "recommendations": [],
        },
        "artifacts": {},
    }

    with TestClient(app) as client:
        token = _token(client)
        task_id = asyncio.run(_insert_task(status=TaskStatus.SUCCEEDED, execution_log=execution_log))
        response = client.get(f"/api/v1/runs/{task_id}", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    triage = response.json()["triage_summary"]

    assert triage["release_risk"]["level"] == "low"
    assert triage["blocking_count"] == 0
    assert triage["blocking_findings"] == []
    assert triage["affected_surfaces"] == []
    assert triage["evidence"]["api_result_count"] == 1
    assert triage["confidence"]["level"] == "high"
    assert triage["reproduction"]["available"] is False
    assert triage["recommended_next_actions"]


def test_run_triage_export_json_shape_and_reusable_assets_without_secrets() -> None:
    with TestClient(app) as client:
        token = _token(client)
        task_id = asyncio.run(
            _insert_task(
                status=TaskStatus.BUG_FOUND,
                execution_log=_triage_export_execution_log(),
                objective="release review password=objective-secret",
                target_url="https://api.example.test/private?debug=target-query-secret",
            )
        )
        suite_id = asyncio.run(_insert_suite(run_id=task_id, name="Release suite token=suite-secret"))
        response = client.get(
            f"/api/v1/runs/{task_id}/triage-export?format=json",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.headers["content-disposition"].endswith('.json"')
    payload = response.json()
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["export_version"] == "triage_export.v1"
    assert payload["run"]["id"] == task_id
    assert payload["run"]["target"] == "https://api.example.test/private"
    assert payload["release_risk"]["level"] == "high"
    assert payload["blocking_count"] >= 1
    assert payload["blocking_findings"][0]["severity"] == "CRITICAL"
    assert payload["evidence_summary"]["api_result_count"] == 1
    assert payload["reusable_assets"]["generated_api_case_count"] == 1
    assert payload["reusable_assets"]["generated_ui_case_count"] == 1
    assert payload["reusable_assets"]["generated_legacy_case_count"] == 1
    assert payload["reusable_assets"]["saved_suite_count"] == 1
    assert payload["reusable_assets"]["saved_case_count"] == 2
    assert payload["reusable_assets"]["saved_suites"][0]["suite_id"] == suite_id
    assert payload["safe_links"]["run_detail_path"] == f"/runs/{task_id}"

    for secret in (
        "setup-secret",
        "auth-secret",
        "auth-header-secret",
        "cookie-secret",
        "csrf-secret",
        "intervention-secret",
        "query-secret",
        "query-token-secret",
        "api-secret",
        "body-secret",
        "request-secret",
        "request-cookie-secret",
        "status-secret",
        "jwt-result-secret",
        "csrf-result-secret",
        "fill-secret",
        "otp-command-secret",
        "stdout-secret",
        "stderr-secret",
        "report-query-secret",
        "debug-query-secret",
        "plain-page-secret",
        "jwt-summary-secret",
        "csrf-summary-secret",
        "xsrf-summary-secret",
        "summary-body-secret",
        "cart-secret",
        "bug-secret",
        "rec-secret",
        "mfa-secret",
        "recommendation-auth-secret",
        "recommendation-cookie-secret",
        "script-secret",
        "objective-secret",
        "target-query-secret",
        "suite-secret",
    ):
        assert secret not in serialized
    for unsafe_label in ("Authorization", "Cookie", "JWT", "CSRF", "XSRF"):
        assert unsafe_label not in serialized
    assert "?debug=" not in serialized
    assert "?cart=" not in serialized
    assert "request_body" not in serialized


def test_run_triage_export_markdown_shape_without_secrets() -> None:
    with TestClient(app) as client:
        token = _token(client)
        task_id = asyncio.run(
            _insert_task(
                status=TaskStatus.BUG_FOUND,
                execution_log=_triage_export_execution_log(),
                target_url="https://api.example.test/private?debug=target-query-secret",
            )
        )
        response = client.get(
            f"/api/v1/runs/{task_id}/triage-export?format=markdown",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.headers["content-disposition"].endswith('.md"')
    body = response.text

    assert "# TestClaw Triage Export" in body
    assert "## Release Risk" in body
    assert "## Blocking Findings" in body
    assert "## Reusable Assets" in body
    assert "GET /private" in body
    for secret in (
        "target-query-secret",
        "query-secret",
        "body-secret",
        "fill-secret",
        "script-secret",
        "debug-query-secret",
        "plain-page-secret",
        "jwt-summary-secret",
        "csrf-summary-secret",
        "xsrf-summary-secret",
        "summary-body-secret",
        "recommendation-auth-secret",
        "recommendation-cookie-secret",
    ):
        assert secret not in body
    for unsafe_label in ("Authorization", "Cookie", "JWT", "CSRF", "XSRF"):
        assert unsafe_label not in body
    assert "?debug=" not in body
    assert "?cart=" not in body


def test_run_triage_export_returns_404_for_unknown_run() -> None:
    with TestClient(app) as client:
        token = _token(client)
        response = client.get(
            "/api/v1/runs/missing-run/triage-export?format=json",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Run not found"


def test_run_triage_export_rejects_invalid_format() -> None:
    with TestClient(app) as client:
        token = _token(client)
        task_id = asyncio.run(
            _insert_task(status=TaskStatus.SUCCEEDED, execution_log={"final_report": {"overall_verdict": "PASS"}})
        )
        response = client.get(
            f"/api/v1/runs/{task_id}/triage-export?format=csv",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "format must be markdown or json"


def test_run_triage_export_route_stays_distinct_from_neighbor_run_routes(monkeypatch) -> None:
    monkeypatch.setattr(runs_api.run_agent_task, "delay", lambda *args, **kwargs: None)

    with TestClient(app) as client:
        token = _token(client)
        task_id = asyncio.run(
            _insert_task(status=TaskStatus.SUCCEEDED, execution_log={"final_report": {"overall_verdict": "PASS"}})
        )
        auth_headers = {"Authorization": f"Bearer {token}"}

        export_response = client.get(
            f"/api/v1/runs/{task_id}/triage-export?format=json",
            headers=auth_headers,
        )
        screenshots_response = client.get(f"/api/v1/runs/{task_id}/screenshots", headers=auth_headers)
        with client.stream("GET", f"/api/v1/runs/{task_id}/stream?token={token}") as stream_response:
            stream_status = stream_response.status_code
            stream_content_type = stream_response.headers.get("content-type", "")
        rerun_response = client.post(f"/api/v1/runs/{task_id}/rerun", headers=auth_headers)
        cancel_response = client.post(f"/api/v1/runs/{task_id}/cancel", headers=auth_headers)
        case_assets_response = client.post(
            f"/api/v1/runs/{task_id}/case-assets",
            headers=auth_headers,
            json={"cases": []},
        )
        intervention_response = client.post(
            f"/api/v1/runs/{task_id}/interventions",
            headers=auth_headers,
            json={"supplemental_instructions": "additional safe context"},
        )

    assert export_response.status_code == 200
    assert export_response.json()["export_version"] == "triage_export.v1"
    assert screenshots_response.status_code == 200
    assert screenshots_response.json() == {"screenshots": []}
    assert stream_status == 200
    assert stream_content_type.startswith("text/event-stream")
    assert rerun_response.status_code == 200
    assert rerun_response.json()["id"] != task_id
    assert cancel_response.status_code == 400
    assert case_assets_response.status_code == 400
    assert intervention_response.status_code == 400

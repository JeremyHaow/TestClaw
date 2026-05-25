import asyncio
import json
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite

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


def test_run_history_insights_uses_projected_execution_log_fields(monkeypatch) -> None:
    now = datetime.utcnow()
    projected_title = "Projected checkout API returns 500 on GET https://api.example.test/checkout"
    heavy_log = _api_failure_log(projected_title, "projected-secret")
    heavy_log["artifacts"].update(
        {
            "ui_screenshots": ["screenshots/run/projected.png"],
            "ui_case_evidence": [
                {
                    "title": "Projected UI case",
                    "status": "failed",
                    "screenshots": ["screenshots/run/projected.png"],
                }
            ],
            "tool_summary": {"total": 17, "failed": 2},
            "ui_reproducible_script": "test('projected', async () => {})",
            "ui_snapshots": ["raw-artifact-snapshot-marker " + ("x" * 5000)],
            "ui_commands": [
                {
                    "command": "snapshot",
                    "stdout": "raw-artifact-stdout-marker " + ("x" * 5000),
                    "stderr": "raw-artifact-stderr-marker",
                }
            ],
            "tool_calls": [
                {"tool": "ui.snapshot", "output_summary": {"stdout": "raw-artifact-tool-marker"}}
                for _ in range(20)
            ],
        }
    )
    heavy_log["tool_calls"] = [
        {"tool": "api.request", "output_summary": {"stdout": "raw-top-level-tool-marker"}}
        for _ in range(20)
    ]
    heavy_log["api_execution_result"].update(
        {
            "total": 33,
            "executed": 32,
            "passed": 30,
            "failed": 1,
            "skipped": 2,
        }
    )
    heavy_log["api_execution_result"]["results"].extend(
        [
            {
                "label": f"Projected passed API {index}",
                "method": "GET",
                "url": f"https://api.example.test/passed/{index}",
                "status_code": 200,
                "passed": True,
                "body": "raw-passed-api-body-marker " + ("x" * 5000),
            }
            for index in range(30)
        ]
    )
    heavy_log["api_execution_result"]["results"].extend(
        [
            {
                "label": f"Projected skipped API {index}",
                "method": "GET",
                "url": f"https://api.example.test/skipped/{index}",
                "skipped": True,
                "passed": False,
                "skip_reason": "raw-skipped-api-marker " + ("x" * 5000),
            }
            for index in range(2)
        ]
    )
    heavy_log["ui_execution_result"] = {
        "total": 31,
        "completed": 31,
        "passed": 30,
        "failed": 1,
        "all_passed": False,
        "screenshots": ["screenshots/run/projected.png"],
        "cases": [
            {
                "title": "Projected UI case",
                "status": "failed",
                "screenshots": ["screenshots/run/projected.png"],
            }
        ]
        + [
            {
                "title": f"Projected passed UI case {index}",
                "status": "passed",
                "passed": True,
                "snapshot": "raw-passed-ui-case-marker " + ("x" * 5000),
            }
            for index in range(30)
        ],
        "snapshot_texts": ["raw-ui-snapshot-marker " + ("x" * 5000)],
        "commands": [
            {
                "command": "snapshot",
                "status_code": 1,
                "passed": False,
                "stdout": "raw-ui-stdout-marker " + ("x" * 5000),
                "stderr": "raw-ui-stderr-marker",
            }
        ]
        + [
            {
                "command": f"passed command {index}",
                "status_code": 0,
                "passed": True,
                "stdout": "raw-passed-command-stdout-marker " + ("x" * 5000),
                "stderr": "raw-passed-command-stderr-marker",
            }
            for index in range(30)
        ],
    }
    heavy_log["progress_events"] = [
        {
            "node": "ui_runner",
            "status": "running",
            "detail": f"verbose progress event {index} password=progress-secret " + ("x" * 5000),
        }
        for index in range(150)
    ]

    with TestClient(app) as client:
        token = _token(client)
        asyncio.run(
            _replace_tasks(
                [
                    {
                        "objective": "projected insights regression",
                        "target_url": "https://api.example.test/checkout?token=target-secret",
                        "status": TaskStatus.FAILED,
                        "test_type": TaskTestType.API,
                        "created_at": now,
                        "execution_log": heavy_log,
                    }
                ]
            )
        )

        from app.api.v1 import runs as runs_api

        sqlite_sql = str(
            select(runs_api._build_history_insight_projection("sqlite")).compile(
                dialect=sqlite.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        ).lower()
        assert "tool_calls" not in sqlite_sql
        assert "results" not in sqlite_sql
        assert "cases" not in sqlite_sql
        assert "commands" not in sqlite_sql
        assert "ui_snapshots" not in sqlite_sql
        assert "ui_commands" not in sqlite_sql
        assert "stdout" not in sqlite_sql
        assert "stderr" not in sqlite_sql

        async def load_projected_tasks():
            async with AsyncSessionLocal() as session:
                return await runs_api._load_run_history_insight_tasks(
                    session,
                    cutoff=now - timedelta(days=30),
                    limit=20,
                )

        projected_tasks = asyncio.run(load_projected_tasks())
        projected_log = projected_tasks[0].execution_log
        projected_serialized = json.dumps(projected_log, ensure_ascii=False)

        assert "tool_calls" not in projected_log
        assert projected_log["tool_summary"]["total"] == 17
        assert set(projected_log["artifacts"]) <= {
            "ui_screenshots",
            "screenshots",
            "tool_summary",
            "ui_reproducible_script",
        }
        assert "ui_snapshots" not in projected_log["artifacts"]
        assert "ui_commands" not in projected_log["artifacts"]
        assert "results" not in projected_log["api_execution_result"]
        assert projected_log["api_execution_result"]["passed"] == 30
        assert projected_log["api_execution_result"]["skipped"] == 2
        assert "cases" not in projected_log["ui_execution_result"]
        assert projected_log["ui_execution_result"]["passed"] == 30
        assert "commands" not in projected_log["ui_execution_result"]
        assert "snapshot_texts" not in projected_log["ui_execution_result"]
        for marker in (
            "raw-artifact-snapshot-marker",
            "raw-artifact-stdout-marker",
            "raw-artifact-stderr-marker",
            "raw-artifact-tool-marker",
            "raw-top-level-tool-marker",
            "raw-passed-api-body-marker",
            "raw-skipped-api-marker",
            "raw-passed-ui-case-marker",
            "raw-ui-snapshot-marker",
            "raw-ui-stdout-marker",
            "raw-ui-stderr-marker",
            "raw-passed-command-stdout-marker",
            "raw-passed-command-stderr-marker",
        ):
            assert marker not in projected_serialized

        def fail_full_parse(_log):
            raise AssertionError("run history insights parsed the full execution_log")

        monkeypatch.setattr(runs_api, "_parse_execution_log_dict", fail_full_parse)
        response = client.get(
            "/api/v1/runs/insights?days=30&limit=20",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    body = response.json()
    serialized = json.dumps(body, ensure_ascii=False)
    assert body["analyzed_runs"] == 1
    assert body["status_counts"]["failed"] == 1
    assert body["evidence_reproduction"]["runs_with_api_evidence"] == 1
    assert body["affected_surfaces"][0]["name"] == "GET /checkout"
    assert "progress-secret" not in serialized


def test_run_history_insights_postgresql_uses_jsonb_projection() -> None:
    from app.api.v1 import runs as runs_api

    captured: dict[str, str] = {}
    now = datetime.utcnow()

    class FakePostgresDb:
        def get_bind(self):
            return SimpleNamespace(dialect=postgresql.dialect())

        async def execute(self, stmt):
            captured["sql"] = str(
                stmt.compile(
                    dialect=postgresql.dialect(),
                    compile_kwargs={"literal_binds": True},
                )
            )
            return [
                SimpleNamespace(
                    id="run-1",
                    target_url="https://api.example.test/checkout?token=target-secret",
                    status=TaskStatus.FAILED,
                    test_type=TaskTestType.API,
                    created_at=now,
                    insight_log={
                        "api_execution_result": {
                            "total": 1,
                            "executed": 1,
                            "passed": 0,
                            "failed": 1,
                        }
                    },
                )
            ]

        async def rollback(self):
            raise AssertionError("PostgreSQL projection should not need fallback rollback")

    tasks = asyncio.run(
        runs_api._load_run_history_insight_tasks(
            FakePostgresDb(),
            cutoff=now - timedelta(days=30),
            limit=20,
        )
    )

    sql = captured["sql"].lower()
    assert len(tasks) == 1
    assert tasks[0].execution_log["api_execution_result"]["failed"] == 1
    assert "jsonb_build_object" in sql
    assert "jsonb_strip_nulls" in sql
    assert "with sampled_history_tasks as materialized" in sql
    assert "cast(tasks.execution_log as jsonb) as log_json" in sql
    assert sql.count("tasks.execution_log") == 1
    assert sql.count("cast(tasks.execution_log as jsonb)") == 1
    assert "sampled_history_tasks.log_json #>" in sql
    assert "jsonb_path_query" not in sql
    assert "history_json_item" not in sql
    assert "like_regex" not in sql
    assert "limit 20" in sql
    assert "tasks.execution_log as execution_log" not in sql
    assert "'artifacts', cast(tasks.execution_log as jsonb)['artifacts']" not in sql
    assert "'results'" not in sql
    assert "'cases'" not in sql
    assert "'commands'" not in sql
    assert "ui_snapshots" not in sql
    assert "ui_commands" not in sql
    assert "tool_calls" not in sql
    assert "stdout" not in sql
    assert "stderr" not in sql

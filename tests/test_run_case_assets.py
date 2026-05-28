import asyncio
import json
import uuid

from fastapi.testclient import TestClient

from app.core.redaction import REDACTED_VALUE
from app.api.v1.test_cases import (
    _build_suite_case_payloads,
    _extract_suite_source_run_target_url,
    _extract_suite_target_url,
)
from app.database import AsyncSessionLocal
from app.main import app
from app.models.task import Task, TaskStatus, TestType as TaskTestType
from app.models.test_case import TestCase as ModelTestCase
from app.models.test_case import TestSuite as ModelTestSuite


def _token(client: TestClient) -> str:
    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "testclaw123"})
    assert login.status_code == 200
    return login.json()["access_token"]


async def _insert_task(execution_log: dict) -> str:
    task_id = str(uuid.uuid4())
    async with AsyncSessionLocal() as session:
        task = Task(
            id=task_id,
            objective="generated case asset run",
            target_url="https://api.example.test",
            status=TaskStatus.SUCCEEDED,
            test_type=TaskTestType.AUTO,
            execution_log=json.dumps(execution_log),
        )
        session.add(task)
        await session.commit()
    return task_id


async def _load_suite_cases(suite_id: str) -> tuple[ModelTestSuite, list[ModelTestCase]]:
    async with AsyncSessionLocal() as session:
        suite = await session.get(ModelTestSuite, suite_id)
        assert suite is not None
        cases: list[ModelTestCase] = []
        for case_id in suite.test_case_ids or []:
            test_case = await session.get(ModelTestCase, case_id)
            assert test_case is not None
            cases.append(test_case)
        return suite, cases


def test_save_run_generated_cases_into_suite() -> None:
    execution_log = {
        "api_cases": [
            {
                "title": "Health API smoke",
                "category": "api",
                "priority": "P1",
                "steps": ["GET /health"],
                "expected": ["HTTP 200"],
                "request_template": {
                    "method": "GET",
                    "path": "/health",
                    "headers": {
                        "Authorization": "Bearer header-secret",
                        "Content-Type": "application/json",
                    },
                    "json": {"password": "body-secret", "safe": "value"},
                },
                "assertions": [{"type": "status_code", "expected": [200, 204]}],
            }
        ],
        "ui_cases": [
            {
                "title": "Open dashboard",
                "category": "ui",
                "priority": "P2",
                "steps": ["Open dashboard"],
                "expected": ["Dashboard is visible"],
                "playwright_commands": [
                    "open https://web.example.test/dashboard",
                    'fill "Password" "ui-secret"',
                    "snapshot",
                ],
            }
        ],
    }

    with TestClient(app) as client:
        token = _token(client)
        run_id = asyncio.run(_insert_task(execution_log))
        response = client.post(
            f"/api/v1/runs/{run_id}/case-assets",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "suite_name": "Accepted smoke suite",
                "cases": [
                    {
                        "source": "api_cases",
                        "index": 0,
                        "case": {
                            "title": "Edited health API",
                            "priority": "P0",
                            "category": "api",
                            "expected": ["HTTP 200 with a JSON body"],
                        },
                    },
                    {
                        "source": "ui_cases",
                        "index": 0,
                        "case": {
                            "title": "Edited dashboard UI",
                            "steps": ["Open the dashboard", "Capture a snapshot"],
                        },
                    },
                ],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["suite_name"] == "Accepted smoke suite"
    assert body["total"] == 2
    assert len(body["case_ids"]) == 2

    suite, cases = asyncio.run(_load_suite_cases(body["suite_id"]))
    assert suite.task_id == run_id
    assert suite.test_case_ids == body["case_ids"]

    api_case = next(case for case in cases if case.category == "api")
    assert api_case.title == "Edited health API"
    assert api_case.priority == "P0"
    assert api_case.expected == ["HTTP 200 with a JSON body"]
    assert api_case.test_data["request_template"]["method"] == "GET"
    assert api_case.test_data["request_template"]["path"] == "/health"
    assert api_case.test_data["request_template"]["base_url"] == "https://api.example.test"
    assert api_case.test_data["base_url"] == "https://api.example.test"
    assert api_case.test_data["request_template"]["headers"] == {"Content-Type": "application/json"}
    assert api_case.test_data["request_template"]["json"]["password"] == REDACTED_VALUE
    assert api_case.test_data["request_template"]["expected_status"] == [200, 204]
    assert api_case.test_data["request_template"]["assertions"] == [
        {"type": "status_code", "expected": [200, 204]}
    ]

    ui_case = next(case for case in cases if case.category == "ui")
    assert ui_case.title == "Edited dashboard UI"
    assert ui_case.steps == ["Open the dashboard", "Capture a snapshot"]
    assert ui_case.test_data["playwright_commands"][0] == "open https://web.example.test/dashboard"
    assert ui_case.test_data["playwright_commands"][1] == f'fill "Password" "{REDACTED_VALUE}"'

    api_cases, ui_cases = _build_suite_case_payloads(cases)
    assert _extract_suite_target_url(api_cases, ui_cases) == "https://api.example.test"


def test_save_run_generated_cases_rejects_invalid_selection() -> None:
    with TestClient(app) as client:
        token = _token(client)
        run_id = asyncio.run(_insert_task({"api_cases": [{"title": "Only case"}]}))
        response = client.post(
            f"/api/v1/runs/{run_id}/case-assets",
            headers={"Authorization": f"Bearer {token}"},
            json={"cases": [{"source": "api_cases", "index": 2}]},
        )

    assert response.status_code == 400
    assert "Invalid case selection" in response.json()["detail"]


def test_suite_target_recovers_legacy_run_case_asset_base_url() -> None:
    run_id = asyncio.run(_insert_task({"api_cases": [{"title": "legacy case"}]}))

    async def _insert_and_resolve() -> str:
        async with AsyncSessionLocal() as session:
            test_case = ModelTestCase(
                title="Legacy run case",
                category="api",
                priority="P1",
                steps=["GET /health"],
                expected=["HTTP 200"],
                test_data={
                    "case_asset": {
                        "version": 1,
                        "source_run_id": run_id,
                        "source": "api_cases",
                        "source_index": 0,
                        "case_type": "api",
                    },
                    "request_template": {"method": "GET", "url": "/health"},
                },
                source=f"run_case_asset:{run_id}:api_cases:0",
            )
            session.add(test_case)
            await session.commit()
            await session.refresh(test_case)
            return await _extract_suite_source_run_target_url(session, [test_case])

    assert asyncio.run(_insert_and_resolve()) == "https://api.example.test"


def test_save_run_generated_cases_does_not_persist_secrets() -> None:
    execution_log = {
        "setup_instructions": "Use password=setup-secret before testing.",
        "api_cases": [
            {
                "title": "token=title-secret",
                "steps": ["Call endpoint with password=step-secret"],
                "expected": ["Authorization: Bearer expected-secret is never saved"],
                "request_template": {
                    "method": "GET",
                    "url": (
                        "https://user:pass-secret@api.example.test/private?"
                        "token=query-secret&session=query-session-secret&auth=query-auth-secret"
                    ),
                    "headers": {
                        "Authorization": "Bearer header-secret",
                        "Cookie": "sid=cookie-secret",
                        "X-Trace-ID": "trace-safe",
                    },
                    "query_params": {
                        "auth": "query-param-auth-secret",
                        "session_id": "query-param-secret",
                        "page": "1",
                    },
                    "body": {
                        "api_key": "body-secret",
                        "session": "body-session-secret",
                        "note": "Bearer body-bearer-secret",
                    },
                },
            }
        ],
        "ui_cases": [
            {
                "title": "Password field check",
                "playwright_commands": [
                    'fill "Password" "ui-secret"',
                    'type "#session" "ui-session-secret"',
                    'fill "#auth" "ui-auth-secret"',
                    'fill "Captcha" "ui-captcha-secret"',
                    'type "#mfa" "ui-mfa-secret"',
                    'fill "#otp" "ui-otp-secret"',
                    "snapshot",
                ],
            }
        ],
    }

    with TestClient(app) as client:
        token = _token(client)
        run_id = asyncio.run(_insert_task(execution_log))
        response = client.post(
            f"/api/v1/runs/{run_id}/case-assets",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "suite_name": "token=suite-secret",
                "cases": [
                    {"source": "api_cases", "index": 0},
                    {"source": "ui_cases", "index": 0},
                ],
            },
        )

    assert response.status_code == 200
    suite, cases = asyncio.run(_load_suite_cases(response.json()["suite_id"]))
    serialized = json.dumps(
        {
            "response": response.json(),
            "suite": {
                "name": suite.name,
                "test_case_ids": suite.test_case_ids,
                "task_id": suite.task_id,
            },
            "cases": [
                {
                    "title": case.title,
                    "steps": case.steps,
                    "expected": case.expected,
                    "test_data": case.test_data,
                    "source": case.source,
                }
                for case in cases
            ],
        },
        ensure_ascii=False,
        default=str,
    )

    for secret in (
        "setup-secret",
        "title-secret",
        "step-secret",
        "expected-secret",
        "query-secret",
        "pass-secret",
        "header-secret",
        "cookie-secret",
        "body-secret",
        "body-session-secret",
        "body-bearer-secret",
        "ui-secret",
        "ui-session-secret",
        "ui-auth-secret",
        "ui-captcha-secret",
        "ui-mfa-secret",
        "ui-otp-secret",
        "suite-secret",
        "query-session-secret",
        "query-auth-secret",
        "query-param-auth-secret",
        "query-param-secret",
    ):
        assert secret not in serialized
    assert "trace-safe" in serialized


def test_save_run_generated_cases_uses_original_source_indexes() -> None:
    execution_log = {
        "api_cases": [
            {"title": "First case", "steps": ["GET /first"]},
            "planner note that is not a case",
            {"title": "Third case", "steps": ["GET /third"]},
        ]
    }

    with TestClient(app) as client:
        token = _token(client)
        run_id = asyncio.run(_insert_task(execution_log))
        response = client.post(
            f"/api/v1/runs/{run_id}/case-assets",
            headers={"Authorization": f"Bearer {token}"},
            json={"cases": [{"source": "api_cases", "index": 2}]},
        )

    assert response.status_code == 200
    _, cases = asyncio.run(_load_suite_cases(response.json()["suite_id"]))
    assert [case.title for case in cases] == ["Third case"]


def test_save_run_generated_cases_rejects_non_dict_case_at_source_index() -> None:
    execution_log = {"api_cases": [{"title": "First case"}, "planner note that is not a case"]}

    with TestClient(app) as client:
        token = _token(client)
        run_id = asyncio.run(_insert_task(execution_log))
        response = client.post(
            f"/api/v1/runs/{run_id}/case-assets",
            headers={"Authorization": f"Bearer {token}"},
            json={"cases": [{"source": "api_cases", "index": 1}]},
        )

    assert response.status_code == 400
    assert "Invalid case selection" in response.json()["detail"]

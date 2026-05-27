import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.agent.action_runtime import append_agent_observation, append_api_result_observations, append_ui_result_observations
from app.agent.nodes import api_runner, execution_evaluator, knowledge_retriever, knowledge_sink, planner, reporter


MOCK_OPENAPI_SCHEMA = [
    {"method": "GET", "path": "/profile", "summary": "Read current profile", "response_status": "200"},
    {
        "method": "POST",
        "path": "/orders",
        "summary": "Create order",
        "request_body_schema": {"type": "object", "properties": {"name": {"type": "string"}}},
        "response_status": "201",
    },
    {
        "method": "GET",
        "path": "/orders/{id}",
        "summary": "Read order detail",
        "path_params": [{"name": "id", "schema": {"type": "integer"}}],
        "response_status": "200",
    },
]

MOCK_UI_PAGE_SNAPSHOT = '- button "Submit order" [ref=e2]\n- textbox "Email" [ref=e3]'


class _FakeResponse:
    status_code = 401
    text = '{"detail":"missing authorization"}'
    headers = {"content-type": "application/json"}
    content = text.encode()

    def json(self) -> dict:
        return {"detail": "missing authorization"}


async def _planned_base(
    *,
    test_type: str,
    target_url: str = "https://api.example.test",
    objective: str | None = None,
) -> dict:
    return await planner.run(
        {
            "test_type": test_type,
            "input_type": "url" if test_type == "ui" else "swagger_json",
            "objective": objective or (
                "Cover all safe GET endpoints for golden task regression"
                if test_type == "api"
                else "Golden task regression"
            ),
            "target_url": target_url,
            "source_input": target_url,
            "api_execution_policy": "safe_read_only",
            "parsed_api_schema": MOCK_OPENAPI_SCHEMA if test_type == "api" else None,
            "workflow_steps": [],
        }
    )


@pytest.mark.asyncio
async def test_golden_api_auth_failure_has_plan_observation_evaluation_and_report(monkeypatch) -> None:
    calls: list[dict] = []

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs) -> _FakeResponse:
            calls.append({"method": method, "url": url, **kwargs})
            return _FakeResponse()

    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)

    state = await _planned_base(test_type="api", objective="Profile auth failure golden regression")
    state["api_cases"] = [
        {
            "title": "Profile requires auth",
            "request_template": {"method": "GET", "path": "/profile"},
            "assertions": [{"type": "status_code", "expected": 200}],
        }
    ]

    state = await api_runner.run(state)
    state = await execution_evaluator.run(state)
    state = await reporter.run(state)

    assert state["api_plan"]["memory_fact_count"] == 0
    assert state["agent_actions"]
    assert [(call["method"], call["url"]) for call in calls] == [
        ("GET", "https://api.example.test/profile")
    ]
    assert state["agent_observations"][0]["failure_type"] == "auth_failure"
    assert state["evidence_evaluation"]["next_action"] == "ask_human"
    assert state["agent_protocol_evaluations"][0]["outcome"] == "needs_human"
    assert state["final_report"]["overall_verdict"] == "FAIL"
    assert any(item["source"] == "api" for item in state["final_report"]["bugs_found"])


@pytest.mark.asyncio
async def test_golden_api_guardrails_cover_safe_write_and_path_dependency(monkeypatch) -> None:
    calls: list[dict] = []

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs):
            calls.append({"method": method, "url": url, **kwargs})
            raise AssertionError("golden guardrail task must not execute blocked requests")

    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)

    state = await _planned_base(test_type="api", objective="Golden API guardrail regression")
    state["api_cases"] = [
        {
            "title": "Unsafe order create",
            "request_template": {"method": "POST", "path": "/orders", "body": {"name": "unsafe"}},
        },
        {
            "title": "Order detail without upstream id",
            "request_template": {
                "method": "GET",
                "path": "/orders/{id}",
                "path_params": [{"name": "id", "schema": {"type": "integer"}}],
            },
        },
    ]

    state = await api_runner.run(state)
    state = await execution_evaluator.run(state)
    state = await reporter.run(state)

    failure_types = {item["failure_type"] for item in state["agent_observations"]}
    assert calls == []
    assert {"safe_write_blocked", "dependency_missing"} <= failure_types
    assert state["agent_protocol_summary"]["by_failure_type"]["safe_write_blocked"] == 1
    assert state["agent_protocol_summary"]["by_failure_type"]["dependency_missing"] == 1
    assert state["evidence_evaluation"]["next_action"] == "replan_api"
    assert state["evidence_evaluation"]["failure_type"] in {"safe_write_blocked", "dependency_missing"}
    assert state["final_report"]["overall_verdict"] in {"FAIL", "NOT_EXECUTED"}


@pytest.mark.asyncio
async def test_golden_api_failure_matrix_classifies_transport_server_and_schema_failures() -> None:
    state = await _planned_base(test_type="api", objective="Golden API failure matrix regression")
    state["agent_execution_stage"] = "api"
    state["api_execution_result"] = {
        "total": 4,
        "executed": 4,
        "passed": 0,
        "failed": 4,
        "skipped": 0,
        "all_passed": False,
        "complete": True,
        "results": [
            {
                "label": "Network failure",
                "method": "GET",
                "url": "https://api.example.test/network",
                "status_code": 0,
                "passed": False,
                "error": "network connection failed",
                "http_executed": True,
            },
            {
                "label": "Timeout failure",
                "method": "GET",
                "url": "https://api.example.test/timeout",
                "status_code": 0,
                "passed": False,
                "error": "request timed out",
                "http_executed": True,
            },
            {
                "label": "Server failure",
                "method": "GET",
                "url": "https://api.example.test/server",
                "status_code": 500,
                "passed": False,
                "failure_type": "backend_error",
                "http_executed": True,
            },
            {
                "label": "Schema failure",
                "method": "GET",
                "url": "https://api.example.test/schema",
                "status_code": 200,
                "passed": False,
                "assertion_results": [
                    {"type": "schema", "passed": False, "blocking": True, "error": "missing id"}
                ],
                "http_executed": True,
            },
        ],
    }
    append_api_result_observations(state, state["api_execution_result"], stage="api_runner")

    failure_types = {item["failure_type"] for item in state["agent_observations"]}

    assert {"network_error", "timeout", "backend_error", "schema_contract"} <= failure_types
    assert state["agent_protocol_summary"]["by_failure_type"]["network_error"] == 1
    assert state["agent_protocol_summary"]["by_failure_type"]["timeout"] == 1
    assert state["agent_protocol_summary"]["by_failure_type"]["backend_error"] == 1
    assert state["agent_protocol_summary"]["by_failure_type"]["schema_contract"] == 1


@pytest.mark.asyncio
async def test_golden_ui_locator_missing_replans_with_evidence_and_report() -> None:
    state = await _planned_base(test_type="ui", target_url="https://app.example.test")
    state["agent_execution_stage"] = "ui"
    state["ui_cases"] = [
        {"title": "Submit order", "playwright_commands": ['click "Missing submit"']}
    ]
    state["ui_execution_result"] = {
        "total": 1,
        "completed": 1,
        "passed": 0,
        "failed": 1,
        "command_total": 1,
        "command_completed": 1,
        "command_failed": 1,
        "screenshots": [{"path": "screenshots/golden/locator-missing.png"}],
        "snapshot_texts": [MOCK_UI_PAGE_SNAPSHOT],
        "all_passed": False,
        "complete": True,
        "commands": [
            {
                "case_index": 0,
                "case_title": "Submit order",
                "command": 'click "Missing submit"',
                "normalized_command": 'click "Missing submit"',
                "status": "executed",
                "status_code": 1,
                "stderr": "locator not found",
                "passed": False,
                "screenshot": "screenshots/golden/locator-missing.png",
            }
        ],
        "cases": [{"case_index": 0, "title": "Submit order", "passed": False}],
    }
    append_ui_result_observations(state, state["ui_execution_result"], stage="ui_runner")

    state = await execution_evaluator.run(state)
    state = await reporter.run(state)

    assert state["ui_plan"]
    assert any(action["tool_name"] == "ui.playwright_cli" for action in state["agent_actions"])
    assert state["agent_observations"][0]["failure_type"] == "ui_locator_missing"
    assert {item["kind"] for item in state["agent_evidence"]} == {"ui_screenshot"}
    assert state["evidence_evaluation"]["next_action"] == "replan_ui"
    assert state["agent_protocol_evaluations"][0]["failure_type"] == "ui_locator_missing"
    assert state["final_report"]["overall_verdict"] == "FAIL"
    assert any(item["source"] == "ui" for item in state["final_report"]["bugs_found"])


@pytest.mark.asyncio
async def test_golden_ui_captcha_blocker_asks_human_and_reports_setup_failure() -> None:
    state = await _planned_base(test_type="ui", target_url="https://app.example.test/login")
    state.update(
        {
            "agent_execution_stage": "ui",
            "setup_instructions": "Log in with dynamic captcha before testing.",
            "setup_result": {"required": True, "status": "failed"},
            "login_verified": False,
            "login_verification_reason": "Dynamic captcha could not be recognized.",
            "last_error": "Dynamic captcha could not be recognized.",
            "ui_captcha_result": {"mode": "dynamic", "recognized": False},
            "ui_execution_result": {
                "total": 0,
                "completed": 0,
                "passed": 0,
                "failed": 0,
                "command_total": 0,
                "command_completed": 0,
                "command_failed": 0,
                "screenshots": [],
                "snapshot_texts": [],
                "all_passed": False,
                "complete": True,
                "commands": [],
                "cases": [],
            },
        }
    )
    append_agent_observation(
        state,
        stage="ui_login",
        layer="ui",
        tool_name="vision.captcha_recognize",
        status="failed",
        outcome="blocked",
        failure_type="ui_setup_failed",
        summary="Dynamic captcha could not be recognized.",
        outputs={"recognized": False},
    )

    state = await execution_evaluator.run(state)
    state = await reporter.run(state)

    assert state["agent_observations"][0]["failure_type"] == "ui_setup_failed"
    assert state["evidence_evaluation"]["next_action"] == "ask_human"
    assert state["evidence_evaluation"]["failure_type"] == "ui_setup_failed"
    assert state["agent_human_question"]
    assert state["final_report"]["overall_verdict"] == "FAIL"
    assert state["final_report"]["bugs_found"][0]["source"] == "ui_setup"


@pytest.mark.asyncio
async def test_golden_ui_high_risk_structured_action_is_blocked_and_asks_human() -> None:
    state = await _planned_base(test_type="ui", target_url="https://app.example.test")
    state["agent_execution_stage"] = "ui"
    state["ui_execution_result"] = {
        "total": 1,
        "completed": 1,
        "passed": 0,
        "failed": 1,
        "command_total": 1,
        "command_completed": 1,
        "command_failed": 1,
        "screenshots": [],
        "snapshot_texts": [MOCK_UI_PAGE_SNAPSHOT],
        "all_passed": False,
        "complete": True,
        "commands": [
            {
                "case_index": 0,
                "case_title": "High risk code",
                "command": "structured run_code",
                "normalized_command": None,
                "status": "blocked",
                "status_code": 1,
                "stderr": "Blocked arbitrary code execution in structured Playwright action.",
                "passed": False,
                "risk": "high_risk",
                "agent_action_type": "run_code",
            }
        ],
        "cases": [{"case_index": 0, "title": "High risk code", "passed": False}],
    }
    append_ui_result_observations(state, state["ui_execution_result"], stage="ui_runner")

    state = await execution_evaluator.run(state)

    assert state["agent_observations"][0]["failure_type"] == "ui_high_risk_action_blocked"
    assert state["evidence_evaluation"]["next_action"] == "ask_human"
    assert state["agent_protocol_evaluations"][0]["outcome"] == "needs_human"


@pytest.mark.asyncio
async def test_golden_rag_memory_hit_is_carried_into_planner() -> None:
    candidate = {
        "schema_version": knowledge_sink.MEMORY_CANDIDATE_SCHEMA,
        "kind": "known_blocker",
        "confidence": "high",
        "source": "execution_evaluation",
        "source_run_id": "golden-memory-run",
        "target_hint": "https://api.example.test/profile",
        "objective": "Profile auth regression",
        "test_type": "api",
        "stage": "api",
        "next_action": "ask_human",
        "sufficient_evidence": False,
        "failure_type": "auth_failure",
        "reason": "Profile endpoint needs auth context.",
        "planner_hint": "Ask for valid Authorization header before running /profile.",
        "facts": [
            {
                "fact_type": "known_blocker",
                "summary": "Profile endpoint needs auth context.",
                "failure_type": "auth_failure",
                "next_action": "ask_human",
                "planner_hint": "Ask for valid Authorization header before running /profile.",
            }
        ],
    }

    class FakeEmbeddingService:
        async def get_client(self, _db):
            return object()

        async def embed_query_with_client(self, _client, _query):
            return [1.0, 0.0]

        async def embed_documents_with_client(self, _client, _texts):
            raise AssertionError("golden memory hit should use stored embedding")

    class FakeResult:
        def scalars(self):
            return [
                SimpleNamespace(
                    id="golden-memory-1",
                    source_script_id="golden-memory-run",
                    content=f"{knowledge_sink.MEMORY_CANDIDATE_MARKER}\n{json.dumps(candidate)}",
                    embedding=[1.0, 0.0],
                    created_at=datetime(2026, 5, 27),
                )
            ]

    class FakeDb:
        async def execute(self, _stmt):
            return FakeResult()

    original_embeddings = knowledge_retriever.embedding_service
    knowledge_retriever.embedding_service = FakeEmbeddingService()
    try:
        retrieved = await knowledge_retriever.run(
            {
                "db_session": FakeDb(),
                "objective": "Profile auth regression",
                "target_url": "https://api.example.test/profile",
                "source_input": "https://api.example.test/profile",
                "test_type": "api",
                "input_type": "swagger_json",
                "parsed_api_schema": MOCK_OPENAPI_SCHEMA,
                "workflow_steps": [],
            }
        )
    finally:
        knowledge_retriever.embedding_service = original_embeddings

    retrieved.pop("db_session", None)
    retrieved["workflow_steps"] = []
    planned = await planner.run(retrieved)

    assert retrieved["rag_retrieval"]["fact_count"] == 1
    assert retrieved["rag_facts"][0]["failure_type"] == "auth_failure"
    assert planned["api_plan"]["memory_fact_count"] == 1
    assert planned["api_plan"]["known_blockers"][0]["source_script_id"] == "golden-memory-run"
    assert "Authorization header" in planned["api_plan"]["known_blockers"][0]["planner_hint"]

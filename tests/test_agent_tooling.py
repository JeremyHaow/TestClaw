import asyncio
import json
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from langchain_openai import OpenAIEmbeddings

from app.agent.json_utils import parse_llm_json
from app.agent.nodes import (
    api_runner,
    agent_supervisor,
    execution_evaluator,
    knowledge_sink,
    knowledge_retriever,
    mission_planner,
    planner,
    reporter,
    source_loader,
    tc_generator,
)
from app.agent.nodes.ui_runner import _build_ui_case_batches
from app.agent.action_runtime import (
    append_api_result_observations,
    append_ui_result_observations,
    validate_agent_action_plan,
    validate_and_record_agent_action_plan,
)
from app.agent.progress import build_execution_log_payload, determine_final_status
from app.agent.runtime.failure_taxonomy import (
    failure_requires_human,
    failure_taxonomy_payload,
    next_action_hint,
    report_category_for_failure,
)
from app.agent.runtime.runtime import AgentRuntime
from app.agent.runtime.event_store import AgentRuntimeEventStore, load_runtime_detail
from app.agent.runtime.tool_executor import ToolExecutor
from app.agent.strategy import normalize_agent_strategy_decision
from app.agent.tool_registry import build_tool_registry, select_skills_for_state
from app.config import settings
from app.core.llm_gateway import LLMGateway
from app.core.redaction import REDACTED_VALUE
from app.models.agent_planning import AgentPlanningMessage
from app.models.llm_provider import LLMProvider, ProviderType
from app.models.task import TaskStatus
from app.services.api_auth import AuthResolution
from app.services import vector_store
from app.services.embedding_service import EmbeddingService, EmbeddingUnavailableError
from app.services.knowledge_service import KnowledgeService
from app.services.agent_planning import normalize_planner_run_payload
from app.services.vector_store import DatabaseKnowledgeVectorStore, MilvusKnowledgeVectorStore
from app.tools.mock_data import generate_mock_json_body
from app.tools.playwright_skill import compile_playwright_ui_action, compile_playwright_ui_actions


def test_tool_registry_selects_api_ui_and_reporting_skills() -> None:
    registry = build_tool_registry()
    skills = select_skills_for_state(
        {
            "test_type": "full",
            "input_type": "url",
            "parsed_api_schema": [
                {"method": "GET", "path": "/health"},
                {
                    "method": "POST",
                    "path": "/users",
                    "request_body_schema": {
                        "type": "object",
                        "properties": {"email": {"type": "string", "format": "email"}},
                        "required": ["email"],
                    },
                },
            ],
            "setup_instructions": "login first",
        }
    )

    skill_names = {skill["name"] for skill in skills}
    tool_names = {tool["name"] for tool in registry["tools"]}
    tools_by_skill = {skill["name"]: set(skill["tools"]) for skill in registry["skills"]}

    assert {
        "agent-supervision",
        "test-planning",
        "api-contract-testing",
        "api-mock-data-generation",
        "browser-ui-testing",
        "test-reporting",
    } <= skill_names
    assert {
        "api.http_request",
        "api.schema_assert",
        "api.generate_mock_json_body",
        "ui.playwright_cli",
        "ui.smart_wait",
    } <= tool_names
    assert "planner.select_agent_strategy" in tools_by_skill["test-planning"]
    assert "api.derive_schema_requests" in tools_by_skill["api-contract-testing"]


def test_mock_json_body_generation_uses_schema_and_faker_values() -> None:
    body = generate_mock_json_body(
        {
            "type": "object",
            "required": ["email", "password", "amount"],
            "properties": {
                "email": {"type": "string", "format": "email"},
                "password": {"type": "string", "minLength": 8},
                "amount": {"type": "number", "minimum": 10},
                "mobile": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
        },
    )

    assert isinstance(body, dict)
    assert "@" in body["email"]
    assert body["password"] == "TestClaw@123456"
    assert body["amount"] >= 10
    assert body["mobile"]
    assert isinstance(body["tags"], list)


def test_status_assertion_one_of_does_not_fallback_to_200() -> None:
    assert api_runner._status_matches("one_of:401,403", 200, {}) is False
    assert api_runner._status_matches("one_of:401,403", 401, {}) is True
    assert api_runner._status_matches("one_of:401,403", 403, {}) is True
    assert api_runner._status_matches("401", 401, {}) is True
    assert api_runner._status_matches("not_equals:200", 200, {}) is False
    assert api_runner._status_matches("not_equals:200", 500, {}) is True
    assert api_runner._status_matches("not-a-status", 200, {}) is False


def test_api_runner_defers_path_param_details_until_list_ids_exist() -> None:
    schema = [
        {"method": "GET", "path": "/wms/warehouse/{id}", "path_params": [{"name": "id", "schema": {"type": "integer"}}]},
        {"method": "GET", "path": "/wms/warehouse/list"},
    ]

    requests = api_runner._build_test_requests(
        schema,
        "https://wms.example.test/api",
        {"Authorization": "Bearer token"},
        "safe_read_only",
    )
    selected, _ = api_runner._select_requests_for_execution(
        requests,
        None,
        source="test",
    )

    assert selected[0]["schema_path"] == "/wms/warehouse/list"
    detail = next(request for request in selected if request["schema_path"] == "/wms/warehouse/{id}")
    assert detail["url"] == "https://wms.example.test/api/wms/warehouse/{{warehouseId}}"
    assert detail["depends_on"] == ["warehouseId"]
    assert api_runner._missing_dependencies(detail, {}) == ["warehouseId"]
    assert api_runner._missing_dependencies(detail, {"warehouseId": 7}) == []
    assert api_runner._substitute_context(detail["url"], {"warehouseId": 7}).endswith("/wms/warehouse/7")


def test_api_runner_extracts_resource_scoped_ids_from_list_response() -> None:
    req = {
        "label": "SMOKE GET /wms/warehouse/list",
        "schema_path": "/wms/warehouse/list",
    }
    payload = {"code": 200, "rows": [{"id": 42, "warehouseName": "A"}], "total": 1}

    updates = api_runner._dependency_context_updates_from_response(req, payload)

    assert updates["id"] == 42
    assert updates["warehouse.id"] == 42
    assert updates["warehouseId"] == 42


def test_api_runner_auth_negative_path_params_do_not_wait_for_list_ids() -> None:
    schema = [
        {
            "method": "GET",
            "path": "/wms/warehouse/{id}",
            "path_params": [{"name": "id", "schema": {"type": "integer"}}],
            "auth_required": True,
        }
    ]

    requests = api_runner._build_test_requests(
        schema,
        "https://wms.example.test/api",
        {"Authorization": "Bearer token"},
        "safe_read_only",
    )

    smoke = next(request for request in requests if request["category"] == "SMOKE")
    unauthorized = next(request for request in requests if request["category"] == "AUTH")

    assert smoke["url"] == "https://wms.example.test/api/wms/warehouse/{{warehouseId}}"
    assert smoke["depends_on"] == ["warehouseId"]
    assert unauthorized["url"] == "https://wms.example.test/api/wms/warehouse/1"
    assert unauthorized.get("depends_on") is None
    assert unauthorized.get("path_param_dependencies") is None


def test_mock_json_body_generation_uses_spring_datetime_strings() -> None:
    body = generate_mock_json_body(
        {
            "type": "object",
            "required": ["createTime", "updatedAt"],
            "properties": {
                "createTime": {"type": "string"},
                "updatedAt": {"type": "string", "format": "date-time"},
            },
        },
    )

    assert "T" not in body["createTime"]
    assert "." not in body["createTime"]
    assert len(body["createTime"]) == 19
    assert "T" not in body["updatedAt"]
    assert len(body["updatedAt"]) == 19


def test_tool_registry_does_not_show_api_chain_for_ui_only_auth_setup() -> None:
    skills = select_skills_for_state(
        {
            "test_type": "ui",
            "input_type": "url",
            "setup_instructions": "login first",
            "auth_chain": {"auth_type": "unknown", "credentials": []},
        }
    )

    skill_names = {skill["name"] for skill in skills}

    assert "browser-ui-testing" in skill_names
    assert "api-chain-orchestration" not in skill_names


def test_tool_registry_selects_rag_skill_after_retrieval() -> None:
    skills = select_skills_for_state(
        {
            "test_type": "api",
            "input_type": "swagger_url",
            "rag_retrieval": {"status": "matched", "sources": [{"id": "k1"}]},
        }
    )

    assert "rag-knowledge-retrieval" in {skill["name"] for skill in skills}


def test_tool_registry_exposes_evidence_evaluation_tool() -> None:
    registry = build_tool_registry()
    planning = next(skill for skill in registry["skills"] if skill["name"] == "test-planning")
    tool_names = {tool["name"] for tool in registry["tools"]}

    assert "planner.evaluate_execution_evidence" in tool_names
    assert "planner.evaluate_execution_evidence" in planning["tools"]


def test_runtime_failure_taxonomy_maps_decisions_and_report_categories() -> None:
    taxonomy = failure_taxonomy_payload()

    assert taxonomy["auth_failure"]["human_required"] is True
    assert failure_requires_human("auth_failure") is True
    assert next_action_hint("ui_locator_missing", layer="ui") == "replan_ui"
    assert report_category_for_failure("safe_write_gate_blocked") == "safety_guardrail"


@pytest.mark.asyncio
async def test_tool_executor_dispatches_api_http_request_with_redaction() -> None:
    class FakeResponse:
        status_code = 200

    class FakeClient:
        async def request(self, method: str, url: str, **kwargs):
            assert method == "GET"
            assert url == "https://api.example.test/profile"
            assert kwargs["headers"]["Authorization"] == "Bearer raw-secret"
            return FakeResponse()

    result = await ToolExecutor({"api_execution_policy": "safe_read_only"}).execute(
        "api.http_request",
        {
            "request": {
                "method": "GET",
                "url": "https://api.example.test/profile",
                "headers": {"Authorization": "Bearer raw-secret"},
            }
        },
        context={"client": FakeClient(), "retry_count": 0, "execution_policy": "safe_read_only"},
    )

    assert result.status == "success"
    assert result.outputs["status_code"] == 200
    assert result.raw["response"].status_code == 200
    assert result.inputs["headers"]["Authorization"] == REDACTED_VALUE


@pytest.mark.asyncio
async def test_agent_runtime_validates_executes_and_evaluates_blocked_action() -> None:
    state = {
        "agent_tool_plan": [
            {
                "tool_name": "api.http_request",
                "inputs": {"method": "POST", "path": "/orders"},
                "reason": "Attempt unsafe write",
            }
        ],
        "parsed_api_schema": [{"method": "POST", "path": "/orders"}],
        "workflow_steps": [],
    }
    runtime = AgentRuntime(state)
    actions = runtime.validate_plan(
        stage="api_runner",
        strategy={"tool_plan": state["agent_tool_plan"]},
        parsed_api_schema=state["parsed_api_schema"],
        execution_policy="safe_read_only",
    )
    observation = await runtime.run_action(actions[0], stage="api_runner")

    assert actions[0]["allowed"] is False
    assert observation["outcome"] == "blocked"
    assert observation["failure_type"] == "safe_write_blocked"
    assert state["agent_protocol_evaluations"][0]["next_action"] == "replan_api"


@pytest.mark.asyncio
async def test_runtime_event_store_persists_protocol_records_to_db() -> None:
    from app.database import AsyncSessionLocal, Base, engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    run_id = str(uuid.uuid4())
    state: dict = {"task_id": run_id}
    observation = append_api_result_observations(
        state,
        {
            "results": [
                {
                    "label": "GET /profile",
                    "method": "GET",
                    "url": "https://api.example.test/profile",
                    "status_code": 401,
                    "passed": False,
                    "failure_type": "auth_failure",
                    "http_executed": True,
                }
            ]
        },
        stage="api_runner",
    )[0]
    state["agent_protocol_evaluations"] = [
        {
            "evaluation_id": "eval-runtime-store",
            "stage": "api",
            "sufficient_evidence": False,
            "outcome": "needs_human",
            "next_action": "ask_human",
            "confidence": "high",
            "failure_type": "auth_failure",
            "reason": "Auth context is missing.",
            "missing_evidence": ["Authorization header"],
            "observation_ids": [observation["observation_id"]],
            "timestamp": datetime.utcnow().isoformat(),
        }
    ]

    async with AsyncSessionLocal() as db:
        await AgentRuntimeEventStore(db).flush_state(state, stage="api_runner")
        detail = await load_runtime_detail(db, run_id)

    assert detail["agent_observations"][0]["failure_type"] == "auth_failure"
    assert detail["agent_protocol_evaluations"][0]["next_action"] == "ask_human"
    assert detail["runtime_events"]


def test_tool_registry_exposes_actionable_skill_and_tool_contracts() -> None:
    registry = build_tool_registry()
    tools = {tool["name"]: tool for tool in registry["tools"]}
    skills = {skill["name"]: skill for skill in registry["skills"]}

    required_skills = {
        "api-auth-discovery",
        "human-intervention",
        "intake-planning",
        "browser-ui-exploration",
        "quality-memory-reuse",
        "evidence-evaluation",
    }
    required_tools = {
        "auth.discover_candidates",
        "auth.try_login",
        "auth.extract_token_or_cookie",
        "auth.validate_readonly",
        "human.ask",
        "intake.update_step",
        "intake.generate_plan",
        "ui.open",
        "ui.snapshot",
        "ui.click",
        "ui.fill",
        "ui.screenshot",
        "ui.assert_visible",
        "memory.retrieve",
        "evidence.evaluate",
    }

    assert required_skills <= set(skills)
    assert required_tools <= set(tools)
    for skill_name in required_skills:
        skill = skills[skill_name]
        assert skill["required_inputs"]
        assert skill["expected_observations"]
        assert skill["failure_recovery"]
        assert skill["safety_constraints"]
        assert set(skill["tools"]) <= set(tools)

    for tool_name in required_tools:
        tool = tools[tool_name]
        assert tool["schema_contract"] == "strict_json_schema"
        assert tool["timeout_ms"] > 0
        assert tool["retry_policy"]["max_attempts"] >= 1
        assert tool["redaction_policy"]
        for schema_key in ("input_schema", "output_schema"):
            schema = tool[schema_key]
            assert schema["type"] == "object"
            assert schema["additionalProperties"] is False
            assert isinstance(schema["properties"], dict)
            assert sorted(schema["properties"]) == sorted(schema["required"])

    assert tools["auth.try_login"]["permission_required"] == "credentials_present"
    assert "password" in tools["auth.try_login"]["redaction_policy"]
    assert tools["ui.fill"]["permission_required"] == "safe_ui_action"


def test_tool_registry_selects_new_supervisor_skills_from_state() -> None:
    api_skills = {
        skill["name"]
        for skill in select_skills_for_state(
            {
                "test_type": "api",
                "input_type": "swagger_json",
                "auth_credentials": {"username": "tester", "password": "secret"},
                "parsed_api_schema": [
                    {"method": "GET", "path": "/me", "auth_required": True}
                ],
                "rag_retrieval": {"status": "matched", "sources": [{"id": "k1"}]},
                "auth_preflight": {
                    "status": "blocked",
                    "missing_fields": ["captcha"],
                },
            }
        )
    }
    ui_skills = {
        skill["name"]
        for skill in select_skills_for_state(
            {
                "test_type": "ui",
                "input_type": "url",
                "ui_seed_url": "https://web.example.test",
            }
        )
    }
    memory_skills = {
        skill["name"]
        for skill in select_skills_for_state(
            {
                "test_type": "api",
                "input_type": "swagger_json",
                "target_memory": {"previous_run_count": 2, "confidence": "medium"},
            }
        )
    }

    assert "api-auth-discovery" in api_skills
    assert "quality-memory-reuse" in api_skills
    assert "human-intervention" in api_skills
    assert "evidence-evaluation" in api_skills
    assert "browser-ui-exploration" in ui_skills
    assert "quality-memory-reuse" in memory_skills


@pytest.mark.asyncio
async def test_agent_supervisor_records_bounded_tool_observations_without_secret_leak() -> None:
    schema = [
        {
            "method": "POST",
            "path": "/login",
            "summary": "Password login",
            "request_body_schema": {
                "type": "object",
                "required": ["username", "password"],
                "properties": {"username": {"type": "string"}, "password": {"type": "string"}},
            },
        },
        {"method": "GET", "path": "/me", "auth_required": True},
        {"method": "POST", "path": "/items"},
    ]
    initial_actions = validate_agent_action_plan(
        [
            {
                "tool_name": "api.derive_schema_requests",
                "inputs": {"scope": "all_documented_safe_methods"},
                "expected_observation": "selected request count",
                "reason": "Select documented read-only API requests.",
            }
        ],
        parsed_api_schema=schema,
        execution_policy="safe_read_only",
    )

    state = await agent_supervisor.run(
        {
            "test_type": "api",
            "input_type": "swagger_json",
            "objective": "验证登录后的资料读取",
            "target_url": "https://api.example.test",
            "api_execution_policy": "safe_read_only",
            "parsed_api_schema": schema,
            "auth_credentials": {"username": "tester", "password": "secret-password"},
            "auth_config": {"enabled": True},
            "auth_preflight": {
                "status": "blocked",
                "missing_fields": ["captcha"],
                "next_action": "请补充验证码后继续。",
            },
            "rag_retrieval": {
                "mode": "vector",
                "status": "matched",
                "sources": [{"id": "k1", "title": "历史阻塞"}],
            },
            "agent_actions": initial_actions,
            "workflow_steps": [],
        }
    )

    observations = state["agent_action_observations"]
    observed_tools = {observation["tool_name"] for observation in observations}
    tool_calls = {call["tool"] for call in state["tool_calls"]}

    assert {
        "api.derive_schema_requests",
        "auth.discover_candidates",
        "human.ask",
        "memory.retrieve",
    } <= observed_tools
    assert "agent.supervisor_loop" in tool_calls
    assert any(
        observation["tool_name"] == "api.derive_schema_requests"
        and observation["status"] == "success"
        and observation["output"]["selected_total"] == 1
        for observation in observations
    )
    assert any(
        observation["tool_name"] == "auth.discover_candidates"
        and observation["status"] == "success"
        and observation["output"]["login_candidate_count"] == 1
        and observation["output"]["validation_candidate_count"] == 1
        for observation in observations
    )
    assert any(
        observation["tool_name"] == "human.ask"
        and observation["status"] == "blocked"
        and observation["output"]["requested_fields"] == ["captcha"]
        for observation in observations
    )
    assert state["auth_discovery"]["login_path"] == "/login"
    assert state["api_request_selection"]["selected"] == [{"method": "GET", "path": "/me"}]
    persisted_trace = json.dumps(
        {
            "observations": state["agent_action_observations"],
            "tool_calls": state["tool_calls"],
            "auth_discovery": state["auth_discovery"],
        },
        ensure_ascii=False,
    )
    assert "secret-password" not in persisted_trace


def test_agent_action_runtime_validates_and_records_model_tool_action() -> None:
    state = {}
    strategy = {
        "intent": "api_focused_endpoints",
        "coverage_scope": "focused_documented_endpoints",
        "method_policy": {"allowed_methods": ["GET"], "write_allowed": False},
        "endpoint_selection": {
            "source": "model_focus",
            "include": [{"method": "get", "path": "health"}],
            "budget_behavior": "focused_only",
        },
        "tool_plan": [
            {
                "tool_name": "api.derive_schema_requests",
                "inputs": {},
                "safety_constraints": ["schema_only"],
                "expected_observation": "request selection",
                "reason": "The objective targets the documented health read.",
            }
        ],
        "reason": "The objective targets one documented read-only endpoint.",
        "source": "llm",
    }

    actions = validate_and_record_agent_action_plan(
        state,
        stage="planner",
        strategy=strategy,
        parsed_api_schema=[{"method": "GET", "path": "/health"}],
        execution_policy="safe_read_only",
    )

    assert actions[0]["allowed"] is True
    assert actions[0]["risk"] == "safety_gate"
    assert actions[0]["inputs"]["scope"] == "focused_documented_endpoints"
    assert actions[0]["inputs"]["include"] == [{"method": "GET", "path": "/health"}]
    assert state["agent_action_observations"][0]["status"] == "validated"
    assert state["agent_react_trace"][-1]["tool"] == "api.derive_schema_requests"
    assert state["agent_react_trace"][-1]["reason"]


def test_agent_action_runtime_blocks_invalid_tool_method_and_path() -> None:
    actions = validate_agent_action_plan(
        [
            {"tool_name": "api.missing_tool", "inputs": {}},
            {
                "tool_name": "api.http_request",
                "inputs": {"method": "POST", "path": "/items"},
            },
            {
                "tool_name": "api.http_request",
                "inputs": {"method": "GET", "path": "/ghost"},
            },
        ],
        parsed_api_schema=[{"method": "GET", "path": "/health"}],
        execution_policy="safe_read_only",
    )

    assert all(action["allowed"] is False for action in actions)
    diagnostics = [item for action in actions for item in action["diagnostics"]]
    assert any(item["kind"] == "unknown_tool_name" for item in diagnostics)
    assert any(
        item["kind"] == "method_blocked_by_policy" and item["method"] == "POST"
        for item in diagnostics
    )
    assert any(
        item["kind"] == "out_of_schema_endpoint" and item["path"] == "/ghost"
        for item in diagnostics
    )


def test_agent_execution_protocol_maps_api_results_without_secret_leak() -> None:
    state: dict = {}

    observations = append_api_result_observations(
        state,
        {
            "results": [
                {
                    "label": "GET /me",
                    "method": "GET",
                    "url": "https://api.example.test/me",
                    "status_code": 200,
                    "elapsed_ms": 12.3,
                    "body": {"name": "Ada", "access_token": "secret-token"},
                    "request_headers": {"Authorization": REDACTED_VALUE},
                    "request_body": None,
                    "passed": True,
                    "category": "SMOKE",
                    "assertion_results": [{"type": "status_code", "passed": True}],
                    "http_executed": True,
                },
                {
                    "label": "GET /admin",
                    "method": "GET",
                    "url": "https://api.example.test/admin",
                    "status_code": 403,
                    "elapsed_ms": 8,
                    "body": {"message": "Forbidden"},
                    "passed": False,
                    "category": "SMOKE",
                    "failure_type": "auth_failure",
                    "failure_reason": "Token lacks scope.",
                    "assertion_results": [{"type": "status_code", "passed": False}],
                    "http_executed": True,
                },
            ]
        },
        stage="api_runner",
    )

    assert len(observations) == 2
    assert observations[0]["status"] == "success"
    assert observations[0]["inputs"]["method"] == "GET"
    assert observations[0]["inputs"]["path"] == "/me"
    assert observations[0]["inputs"]["url"] == "https://api.example.test/me"
    assert observations[0]["outputs"]["duration_ms"] == 12.3
    assert observations[0]["outputs"]["assertions"] == [
        {
            "type": "status_code",
            "passed": True,
            "skipped": False,
            "blocking": True,
        }
    ]
    assert observations[0]["outputs"]["error_type"] is None
    assert observations[0]["outputs"]["failure_type"] is None
    assert observations[0]["outputs"]["safety_decision"] == "safe_read_executed"
    assert observations[0]["outputs"]["evidence_refs"] == observations[0]["evidence_ids"]
    assert observations[1]["failure_type"] == "auth_failure"
    assert observations[1]["outcome"] == "failed"
    assert observations[1]["outputs"]["error_type"] == "auth_failure"
    assert observations[1]["outputs"]["failure_type"] == "auth_failure"
    assert observations[1]["outputs"]["raw_failure_type"] == "auth_failure"
    assert observations[1]["outputs"]["safety_decision"] == "safe_read_executed"
    assert state["agent_protocol_summary"]["observation_total"] == 2
    assert state["agent_protocol_summary"]["by_failure_type"]["auth_failure"] == 1
    assert {item["kind"] for item in state["agent_evidence"]} == {
        "api_response",
        "api_assertion",
    }
    assert "secret-token" not in json.dumps(state, ensure_ascii=False)


def test_agent_execution_protocol_normalizes_api_failure_taxonomy() -> None:
    state: dict = {"api_execution_policy": "write_allowed"}

    observations = append_api_result_observations(
        state,
        {
            "results": [
                {
                    "label": "POST /items",
                    "method": "POST",
                    "url": "https://api.example.test/items",
                    "status_code": None,
                    "elapsed_ms": 0,
                    "passed": None,
                    "skipped": True,
                    "skip_type": api_runner.SAFE_WRITE_BLOCK_SKIP_TYPE,
                    "failure_type": api_runner.SAFE_WRITE_BLOCK_SKIP_TYPE,
                    "skip_reason": "write_allowed still requires the safe-write gate",
                    "http_executed": False,
                    "assertion_results": [],
                },
                {
                    "label": "GET /items/{id}",
                    "method": "GET",
                    "url": "https://api.example.test/items/{id}",
                    "status_code": None,
                    "elapsed_ms": 0,
                    "passed": None,
                    "skipped": True,
                    "skip_type": api_runner.PATH_PARAM_UNRESOLVED_SKIP_TYPE,
                    "skip_reason": "missing path dependency id",
                    "http_executed": False,
                    "assertion_results": [],
                },
                {
                    "label": "GET /slow",
                    "method": "GET",
                    "url": "https://api.example.test/slow",
                    "status_code": 0,
                    "elapsed_ms": 0,
                    "passed": False,
                    "error": "Request timed out after 30s",
                    "http_executed": True,
                    "assertion_results": [],
                },
                {
                    "label": "GET /dns",
                    "method": "GET",
                    "url": "https://missing.example.test/dns",
                    "status_code": 0,
                    "elapsed_ms": 0,
                    "passed": False,
                    "error": "ConnectError: DNS name resolution failed",
                    "http_executed": True,
                    "assertion_results": [],
                },
                {
                    "label": "GET /shape",
                    "method": "GET",
                    "url": "https://api.example.test/shape",
                    "status_code": 200,
                    "elapsed_ms": 4,
                    "passed": False,
                    "http_executed": True,
                    "assertion_results": [
                        {
                            "type": "schema",
                            "passed": False,
                            "blocking": True,
                            "error": "required property missing",
                        }
                    ],
                },
                {
                    "label": "GET /field",
                    "method": "GET",
                    "url": "https://api.example.test/field",
                    "status_code": 200,
                    "elapsed_ms": 5,
                    "passed": False,
                    "http_executed": True,
                    "assertion_results": [
                        {
                            "type": "json_path",
                            "path": "$.ok",
                            "expected": True,
                            "actual": False,
                            "passed": False,
                        }
                    ],
                },
            ]
        },
        stage="api_runner",
    )

    assert [item["failure_type"] for item in observations] == [
        "safe_write_blocked",
        "dependency_missing",
        "timeout",
        "network_error",
        "schema_contract",
        "assertion_failure",
    ]
    assert observations[0]["outputs"]["raw_failure_type"] == api_runner.SAFE_WRITE_BLOCK_SKIP_TYPE
    assert observations[0]["outputs"]["safety_decision"] == "blocked_by_safe_write_gate"
    assert observations[1]["outputs"]["skip_type"] == api_runner.PATH_PARAM_UNRESOLVED_SKIP_TYPE
    assert observations[1]["outputs"]["safety_decision"] == "blocked_by_missing_dependency"
    assert observations[2]["outputs"]["error_type"] == "timeout"
    assert observations[3]["outputs"]["error_type"] == "network_error"
    assert observations[4]["outputs"]["assertions"][0]["error"] == "required property missing"
    assert observations[5]["outputs"]["assertions"][0]["path"] == "$.ok"
    assert state["agent_protocol_summary"]["by_failure_type"]["safe_write_blocked"] == 1
    assert state["agent_protocol_summary"]["by_failure_type"]["dependency_missing"] == 1


def test_agent_execution_protocol_maps_ui_results_and_evidence() -> None:
    state: dict = {}

    observations = append_ui_result_observations(
        state,
        {
            "commands": [
                {
                    "case_index": 0,
                    "case_title": "Open dashboard",
                    "command": "snapshot",
                    "normalized_command": "snapshot",
                    "status": "executed",
                    "status_code": 0,
                    "stdout": "- button \"Continue\" [ref=e5]",
                    "stderr": "",
                    "passed": True,
                },
                {
                    "case_index": 0,
                    "case_title": "Open dashboard",
                    "command": "click \"Missing\"",
                    "normalized_command": "click \"Missing\"",
                    "status": "executed",
                    "status_code": 1,
                    "stdout": "",
                    "stderr": "locator not found",
                    "passed": False,
                    "screenshot": "/tmp/failure.png",
                    "screenshot_evidence": {"label": "failure screenshot"},
                },
            ]
        },
        stage="ui_runner",
    )

    assert len(observations) == 2
    assert observations[0]["status"] == "success"
    assert observations[1]["failure_type"] == "ui_locator_missing"
    assert observations[1]["tool_call_ids"]
    assert state["agent_protocol_summary"]["by_layer"]["ui"] == 2
    assert {item["kind"] for item in state["agent_evidence"]} == {
        "ui_snapshot",
        "ui_screenshot",
    }


def test_execution_log_payload_preserves_agent_protocol_records() -> None:
    state = {
        "agent_observations": [{"observation_id": "obs-1", "stage": "api_runner"}],
        "agent_tool_calls": [{"tool_call_id": "tool-1", "tool_name": "api.http_request"}],
        "agent_evidence": [{"evidence_id": "evidence-1", "kind": "api_response"}],
        "agent_protocol_evaluations": [{"evaluation_id": "eval-1", "next_action": "report"}],
        "agent_protocol_summary": {"observation_total": 1},
        "agent_retry_counts": {"api": 1},
        "agent_retry_feedback": "Retry same request batch.",
        "agent_human_question": "Please provide valid login details.",
        "rag_facts": [{"fact_type": "known_blocker", "confidence": "high"}],
        "memory_candidates": [{"kind": "known_blocker", "confidence": "high"}],
    }

    payload = build_execution_log_payload(state)

    assert payload["agent_observations"][0]["observation_id"] == "obs-1"
    assert payload["agent_tool_calls"][0]["tool_call_id"] == "tool-1"
    assert payload["agent_evidence"][0]["evidence_id"] == "evidence-1"
    assert payload["agent_protocol_evaluations"][0]["evaluation_id"] == "eval-1"
    assert payload["agent_protocol_summary"]["observation_total"] == 1
    assert payload["agent_retry_counts"] == {"api": 1}
    assert payload["agent_retry_feedback"] == "Retry same request batch."
    assert payload["agent_human_question"] == "Please provide valid login details."
    assert payload["rag_facts"][0]["fact_type"] == "known_blocker"
    assert payload["memory_candidates"][0]["kind"] == "known_blocker"


@pytest.mark.asyncio
async def test_mission_planner_decomposes_complex_objective_and_persists_trace() -> None:
    state = await mission_planner.run(
        {
            "objective": "Log in, verify dashboard metrics, test item search, and validate API health",
            "target_url": "https://app.example.test/login",
            "ui_seed_url": "https://app.example.test/login",
            "source_input": "https://app.example.test/login",
            "input_type": "url",
            "test_type": "full",
            "parsed_api_schema": [{"method": "GET", "path": "/health", "summary": "Health"}],
            "api_execution_policy": "safe_read_only",
            "workflow_steps": [],
        }
    )

    mission = state["agent_mission_plan"]
    role_names = {role["role"] for role in state["agent_roster"]}
    delegated_roles = {item["to"] for item in state["agent_delegation_trace"]}
    react_trace = state["agent_react_trace"]
    payload = build_execution_log_payload(state)

    assert mission["control_pattern"] == "plan_execute_react"
    assert len(mission["subgoals"]) >= 7
    assert mission["memory_needs"]
    assert any(need["need"] == "browser_surface" for need in mission["environment_needs"])
    assert {"supervisor_planner", "memory_researcher", "api_executor", "ui_explorer"} <= role_names
    assert {"memory_researcher", "api_executor", "ui_explorer", "evidence_evaluator"} <= delegated_roles
    assert react_trace[-1]["action"] == "agent.create_mission_plan"
    assert react_trace[-1]["reason"]
    assert react_trace[-1]["observation"]
    assert payload["agent_mission_plan"]["subgoals"] == mission["subgoals"]
    assert payload["agent_roster"] == state["agent_roster"]
    assert payload["agent_delegation_trace"] == state["agent_delegation_trace"]
    assert payload["agent_react_trace"] == state["agent_react_trace"]


def test_vector_store_selects_default_database_backend(monkeypatch) -> None:
    monkeypatch.setattr(vector_store.settings, "RAG_VECTOR_STORE_BACKEND", "database")

    store = vector_store.get_knowledge_vector_store()

    assert isinstance(store, DatabaseKnowledgeVectorStore)
    assert store.backend_info()["active"] == "database"


def test_vector_store_selects_milvus_config_without_runtime_dependency(monkeypatch) -> None:
    monkeypatch.setattr(vector_store.settings, "RAG_VECTOR_STORE_BACKEND", "milvus")
    monkeypatch.setattr(vector_store.settings, "MILVUS_URI", "http://milvus.example.test:19530")
    monkeypatch.setattr(vector_store.settings, "MILVUS_COLLECTION", "testclaw_agent_memory")

    store = vector_store.get_knowledge_vector_store()
    info = store.backend_info()

    assert isinstance(store, MilvusKnowledgeVectorStore)
    assert info["requested"] == "milvus"
    assert info["collection"] == "testclaw_agent_memory"
    assert "dependency_available" in info


def test_llm_json_parser_handles_fenced_extra_text_and_near_json() -> None:
    fenced = "Here is the result:\n```json\n{\"api_cases\": [], \"ui_cases\": []}\n```\nDone"
    extra = "prefix {\"next_action\": \"report\", \"diagnostics\": []} suffix"
    near_json = "{\"api_cases\": [] \"ui_cases\": []}"
    non_json_fence = "```text\nnot json\n```\nThen {\"next_action\": \"report\"}"
    partial = "{\"api_cases\": ["

    assert parse_llm_json(fenced, expected="object") == {"api_cases": [], "ui_cases": []}
    assert parse_llm_json(extra, expected="object") == {"next_action": "report", "diagnostics": []}
    assert parse_llm_json(near_json, expected="object") == {"api_cases": [], "ui_cases": []}
    assert parse_llm_json(non_json_fence, expected="object") == {"next_action": "report"}
    assert parse_llm_json(partial, expected="object") is None


@pytest.mark.asyncio
async def test_execution_evaluator_replans_api_when_no_requests_were_built(monkeypatch) -> None:
    monkeypatch.setattr(execution_evaluator.settings, "AGENT_MAX_REPLAN_ATTEMPTS", 2)

    state = await execution_evaluator.run(
        {
            "agent_execution_stage": "api",
            "test_type": "api",
            "input_type": "swagger_json",
            "objective": "API smoke",
            "target_url": "https://api.example.test",
            "parsed_api_schema": [{"method": "GET", "path": "/health"}],
            "api_cases": [{"title": "bad generated case"}],
            "api_execution_result": {
                "total": 0,
                "executed": 0,
                "passed": 0,
                "failed": 0,
                "skipped": 0,
                "complete": True,
            },
            "workflow_steps": [],
        }
    )

    assert state["agent_next_node"] == "tc_generator"
    assert state["evidence_evaluation"]["next_action"] == "replan_api"
    assert state["agent_replan_counts"]["api"] == 1
    assert state["api_cases"] == []
    assert state["agent_replan_feedback"]
    assert "planner.evaluate_execution_evidence" in {call["tool"] for call in state["tool_calls"]}


@pytest.mark.asyncio
async def test_execution_evaluator_continues_to_ui_after_api_evidence() -> None:
    state = await execution_evaluator.run(
        {
            "agent_execution_stage": "api",
            "test_type": "full",
            "input_type": "url",
            "objective": "Full smoke",
            "target_url": "https://app.example.test",
            "ui_seed_url": "https://app.example.test",
            "api_execution_result": {
                "total": 1,
                "executed": 1,
                "passed": 1,
                "failed": 0,
                "skipped": 0,
                "http_executed": 1,
                "all_passed": True,
                "complete": True,
            },
            "workflow_steps": [],
        }
    )

    assert state["agent_next_node"] == "ui_login"
    assert state["evidence_evaluation"]["next_action"] == "continue_to_ui"


@pytest.mark.asyncio
async def test_execution_evaluator_does_not_continue_to_ui_after_api_replan_limit(monkeypatch) -> None:
    class FakeMessage:
        content = json.dumps(
            {
                "sufficient_evidence": True,
                "confidence": "high",
                "next_action": "continue_to_ui",
                "reason": "Continue to UI.",
                "diagnostics": [],
                "missing_evidence": [],
                "replan_instructions": "",
            }
        )

    class FakePlanner:
        async def ainvoke(self, _messages):
            return FakeMessage()

    async def fake_get_planner(_db):
        return FakePlanner()

    monkeypatch.setattr(execution_evaluator.settings, "AGENT_MAX_REPLAN_ATTEMPTS", 1)
    monkeypatch.setattr(execution_evaluator.llm_gateway, "get_planner", fake_get_planner)

    state = await execution_evaluator.run(
        {
            "db_session": object(),
            "agent_execution_stage": "api",
            "test_type": "full",
            "input_type": "url",
            "objective": "Full smoke",
            "target_url": "https://app.example.test",
            "ui_seed_url": "https://app.example.test",
            "parsed_api_schema": [{"method": "GET", "path": "/health"}],
            "api_execution_result": {
                "total": 0,
                "executed": 0,
                "passed": 0,
                "failed": 0,
                "skipped": 0,
                "complete": True,
            },
            "agent_replan_counts": {"api": 1},
            "workflow_steps": [],
        }
    )

    assert state["agent_next_node"] == "reporter"
    assert state["evidence_evaluation"]["next_action"] == "report"
    assert state["evidence_evaluation"]["sufficient_evidence"] is False
    assert "Replan limit reached" in state["evidence_evaluation"]["reason"]


@pytest.mark.asyncio
async def test_execution_evaluator_retries_api_after_transient_observation(monkeypatch) -> None:
    monkeypatch.setattr(execution_evaluator.settings, "AGENT_MAX_REPLAN_ATTEMPTS", 2)
    initial_state = {
        "agent_execution_stage": "api",
        "test_type": "api",
        "input_type": "swagger_json",
        "objective": "API smoke",
        "target_url": "https://api.example.test",
        "api_execution_result": {
            "total": 1,
            "executed": 1,
            "passed": 0,
            "failed": 1,
            "skipped": 0,
            "http_executed": 1,
            "all_passed": False,
            "complete": True,
            "results": [
                {
                    "label": "GET /health",
                    "method": "GET",
                    "url": "https://api.example.test/health",
                    "status_code": 0,
                    "elapsed_ms": 0,
                    "passed": False,
                    "error": "ConnectError: DNS name resolution failed",
                    "http_executed": True,
                    "assertion_results": [],
                }
            ],
        },
        "workflow_steps": [],
    }
    append_api_result_observations(
        initial_state,
        initial_state["api_execution_result"],
        stage="api_runner",
    )

    state = await execution_evaluator.run(initial_state)

    assert state["agent_next_node"] == "api_runner"
    assert state["evidence_evaluation"]["next_action"] == "retry_same_action"
    assert state["evidence_evaluation"]["failure_type"] == "network_error"
    assert state["evidence_evaluation"]["replan_hint"]
    assert state["agent_retry_counts"]["api"] == 1
    assert state["agent_retry_feedback"]
    assert state["agent_attempt_history"][0]["attempt_kind"] == "retry"
    assert state["agent_protocol_evaluations"][0]["outcome"] == "needs_retry"
    assert state["agent_protocol_evaluations"][0]["failure_type"] == "network_error"


@pytest.mark.asyncio
async def test_execution_evaluator_retries_ui_after_timeout_observation(monkeypatch) -> None:
    monkeypatch.setattr(execution_evaluator.settings, "AGENT_MAX_REPLAN_ATTEMPTS", 2)
    initial_state = {
        "agent_execution_stage": "ui",
        "test_type": "ui",
        "input_type": "url",
        "objective": "UI smoke",
        "target_url": "https://app.example.test",
        "ui_execution_result": {
            "total": 1,
            "completed": 1,
            "passed": 0,
            "failed": 1,
            "command_total": 1,
            "command_completed": 1,
            "command_failed": 1,
            "screenshots": [],
            "snapshot_texts": [],
            "all_passed": False,
            "complete": True,
            "commands": [
                {
                    "case_index": 0,
                    "case_title": "Open app",
                    "command": "goto https://app.example.test",
                    "normalized_command": "goto https://app.example.test",
                    "status": "executed",
                    "status_code": 1,
                    "stderr": "Timeout 30000ms exceeded",
                    "passed": False,
                }
            ],
        },
        "workflow_steps": [],
    }
    append_ui_result_observations(
        initial_state,
        initial_state["ui_execution_result"],
        stage="ui_runner",
    )

    state = await execution_evaluator.run(initial_state)

    assert state["agent_next_node"] == "ui_runner"
    assert state["evidence_evaluation"]["next_action"] == "retry_same_action"
    assert state["evidence_evaluation"]["failure_type"] == "timeout"
    assert state["agent_retry_counts"]["ui"] == 1
    assert state["agent_attempt_history"][0]["attempt_kind"] == "retry"
    assert state["agent_protocol_evaluations"][0]["outcome"] == "needs_retry"


@pytest.mark.asyncio
async def test_execution_evaluator_asks_human_for_api_auth_observation() -> None:
    initial_state = {
        "agent_execution_stage": "api",
        "test_type": "api",
        "input_type": "swagger_json",
        "objective": "API smoke",
        "target_url": "https://api.example.test",
        "api_execution_result": {
            "total": 1,
            "executed": 1,
            "passed": 0,
            "failed": 1,
            "skipped": 0,
            "http_executed": 1,
            "all_passed": False,
            "complete": True,
            "results": [
                {
                    "label": "GET /me",
                    "method": "GET",
                    "url": "https://api.example.test/me",
                    "status_code": 401,
                    "elapsed_ms": 12,
                    "passed": False,
                    "failure_type": "auth_failure",
                    "failure_reason": "Missing Authorization header.",
                    "http_executed": True,
                    "assertion_results": [{"type": "status_code", "passed": False}],
                }
            ],
        },
        "workflow_steps": [],
    }
    append_api_result_observations(
        initial_state,
        initial_state["api_execution_result"],
        stage="api_runner",
    )

    state = await execution_evaluator.run(initial_state)

    assert state["agent_next_node"] == "reporter"
    assert state["evidence_evaluation"]["next_action"] == "ask_human"
    assert state["evidence_evaluation"]["failure_type"] == "auth_failure"
    assert state["evidence_evaluation"]["human_question"]
    assert state["agent_human_question"] == state["evidence_evaluation"]["human_question"]
    assert state["agent_protocol_evaluations"][0]["outcome"] == "needs_human"
    assert state["agent_protocol_evaluations"][0]["failure_type"] == "auth_failure"


@pytest.mark.asyncio
async def test_execution_evaluator_replans_api_from_dependency_observation(monkeypatch) -> None:
    monkeypatch.setattr(execution_evaluator.settings, "AGENT_MAX_REPLAN_ATTEMPTS", 2)
    initial_state = {
        "agent_execution_stage": "api",
        "test_type": "api",
        "input_type": "swagger_json",
        "objective": "Fetch order detail after listing orders",
        "target_url": "https://api.example.test",
        "parsed_api_schema": [
            {"method": "GET", "path": "/orders"},
            {"method": "GET", "path": "/orders/{id}"},
        ],
        "api_cases": [{"title": "order detail"}],
        "api_execution_result": {
            "total": 1,
            "executed": 0,
            "passed": 0,
            "failed": 1,
            "skipped": 1,
            "http_executed": 0,
            "all_passed": False,
            "complete": True,
            "results": [
                {
                    "label": "GET /orders/{id}",
                    "method": "GET",
                    "url": "https://api.example.test/orders/{id}",
                    "status_code": None,
                    "elapsed_ms": 0,
                    "passed": None,
                    "skipped": True,
                    "skip_type": api_runner.PATH_PARAM_UNRESOLVED_SKIP_TYPE,
                    "skip_reason": "缺少可用于路径参数 id 的上游列表响应值。",
                    "http_executed": False,
                    "assertion_results": [],
                }
            ],
        },
        "workflow_steps": [],
    }
    append_api_result_observations(
        initial_state,
        initial_state["api_execution_result"],
        stage="api_runner",
    )

    state = await execution_evaluator.run(initial_state)

    assert state["agent_next_node"] == "tc_generator"
    assert state["evidence_evaluation"]["next_action"] == "replan_api"
    assert state["evidence_evaluation"]["failure_type"] == "dependency_missing"
    assert state["evidence_evaluation"]["missing_evidence"]
    assert state["agent_replan_counts"]["api"] == 1
    assert state["agent_attempt_history"][0]["attempt_kind"] == "replan"
    assert state["agent_protocol_evaluations"][0]["failure_type"] == "dependency_missing"


@pytest.mark.asyncio
async def test_execution_evaluator_replans_ui_after_single_shallow_failure(monkeypatch) -> None:
    monkeypatch.setattr(execution_evaluator.settings, "AGENT_MAX_REPLAN_ATTEMPTS", 2)

    initial_state = {
        "agent_execution_stage": "ui",
        "test_type": "ui",
        "input_type": "url",
        "objective": "UI smoke",
        "target_url": "https://app.example.test",
        "ui_cases": [
            {"title": "Click missing action", "playwright_commands": ["click \"Missing\""]}
        ],
        "ui_execution_result": {
            "total": 1,
            "completed": 1,
            "passed": 0,
            "failed": 1,
            "command_total": 1,
            "command_completed": 1,
            "command_failed": 1,
            "screenshots": [],
            "snapshot_texts": ["- button \"Real action\" [ref=e2]"],
            "all_passed": False,
            "complete": True,
            "commands": [
                {
                    "case_index": 0,
                    "case_title": "Click missing action",
                    "command": "click \"Missing\"",
                    "status_code": 1,
                    "stderr": "locator not found",
                    "passed": False,
                }
            ],
        },
        "workflow_steps": [],
    }
    append_ui_result_observations(initial_state, initial_state["ui_execution_result"], stage="ui_runner")

    state = await execution_evaluator.run(initial_state)

    assert state["agent_next_node"] == "ui_test_planner"
    assert state["evidence_evaluation"]["next_action"] == "replan_ui"
    assert state["agent_replan_counts"]["ui"] == 1
    assert state["ui_cases"] == []
    assert state["agent_attempt_history"][0]["stage"] == "ui"
    assert state["agent_protocol_evaluations"][0]["next_action"] == "replan_ui"
    assert state["agent_protocol_evaluations"][0]["failure_type"] == "ui_locator_missing"
    assert state["evidence_evaluation"]["summary"]["agent_protocol_stage"]["failure_types"] == {
        "ui_locator_missing": 1
    }


@pytest.mark.asyncio
async def test_execution_evaluator_uses_planner_model_when_available(monkeypatch) -> None:
    class FakeMessage:
        content = json.dumps(
            {
                "sufficient_evidence": False,
                "confidence": "high",
                "next_action": "replan_ui",
                "reason": "Need current refs from snapshot before stopping.",
                "diagnostics": ["Use visible refs"],
                "missing_evidence": ["No screenshot"],
                "replan_instructions": "Generate commands from the latest snapshot refs.",
            }
        )

    class FakePlanner:
        calls = 0

        async def ainvoke(self, _messages):
            self.calls += 1
            return FakeMessage()

    fake_planner = FakePlanner()

    async def fake_get_planner(_db):
        return fake_planner

    monkeypatch.setattr(execution_evaluator.llm_gateway, "get_planner", fake_get_planner)

    state = await execution_evaluator.run(
        {
            "db_session": object(),
            "agent_execution_stage": "ui",
            "test_type": "ui",
            "input_type": "url",
            "objective": "UI smoke",
            "target_url": "https://app.example.test",
            "ui_execution_result": {
                "total": 1,
                "completed": 1,
                "passed": 0,
                "failed": 1,
                "command_total": 1,
                "command_completed": 1,
                "command_failed": 1,
                "screenshots": [],
                "snapshot_texts": ["- button \"Continue\" [ref=e5]"],
                "all_passed": False,
                "complete": True,
                "commands": [
                    {
                        "case_index": 0,
                        "case_title": "bad selector",
                        "command": "click \"Missing\"",
                        "status_code": 1,
                        "stderr": "not found",
                        "passed": False,
                    }
                ],
            },
            "workflow_steps": [],
        }
    )

    assert fake_planner.calls == 1
    assert state["evidence_evaluation"]["source"] == "llm+guardrail"
    assert state["agent_replan_feedback"] == "Generate commands from the latest snapshot refs."
    call = next(call for call in state["tool_calls"] if call["tool"] == "planner.evaluate_execution_evidence")
    assert call["output"]["source"] == "llm+guardrail"


@pytest.mark.asyncio
async def test_execution_evaluator_sanitizes_api_replan_scope(monkeypatch) -> None:
    class FakeMessage:
        content = json.dumps(
            {
                "sufficient_evidence": False,
                "confidence": "high",
                "next_action": "replan_api",
                "reason": "Add body assertions and call /non_existent_endpoint.",
                "diagnostics": ["Try /non_existent_endpoint"],
                "missing_evidence": [],
                "replan_instructions": "Add assertions on /get and negative probe /non_existent_endpoint.",
            }
        )

    class FakePlanner:
        async def ainvoke(self, _messages):
            return FakeMessage()

    async def fake_get_planner(_db):
        return FakePlanner()

    monkeypatch.setattr(execution_evaluator.llm_gateway, "get_planner", fake_get_planner)
    monkeypatch.setattr(execution_evaluator.settings, "AGENT_MAX_REPLAN_ATTEMPTS", 2)

    state = await execution_evaluator.run(
        {
            "db_session": object(),
            "agent_execution_stage": "api",
            "test_type": "api",
            "input_type": "swagger_json",
            "objective": "httpbin smoke",
            "target_url": "https://httpbin.org",
            "api_execution_policy": "safe_read_only",
            "parsed_api_schema": [
                {"method": "GET", "path": "/get", "response_status": "200"},
                {"method": "GET", "path": "/headers", "response_status": "200"},
            ],
            "api_execution_result": {
                "total": 2,
                "executed": 2,
                "passed": 2,
                "failed": 0,
                "skipped": 0,
                "http_executed": 2,
                "all_passed": True,
                "complete": True,
                "results": [
                    {"method": "GET", "url": "https://httpbin.org/get", "passed": True},
                    {"method": "GET", "url": "https://httpbin.org/headers", "passed": True},
                ],
            },
            "workflow_steps": [],
        }
    )

    feedback = state["agent_replan_feedback"]

    assert state["agent_next_node"] == "tc_generator"
    assert "GET /get" in feedback
    assert "GET /headers" in feedback
    assert "/non_existent_endpoint" not in feedback
    assert "out-of-schema" in feedback


@pytest.mark.asyncio
async def test_generated_api_cases_are_bounded_to_openapi_scope_before_execution(monkeypatch) -> None:
    class FakePlannerMessage:
        content = """
        ```json
        {
          "api_cases": [
            {
              "title": "documented get with unsupported assertion",
              "endpoint": "/get",
              "method": "GET",
              "case_type": "api",
              "category": "SMOKE",
              "request_template": {"method": "GET", "path": "/get"},
              "assertions": [
                {"type": "status_code", "expected": 200},
                {"type": "json_path", "path": "$.must_not_be_required", "expected": "not_null"}
              ]
            },
            {
              "title": "invented negative path",
              "endpoint": "/non_existent_endpoint",
              "method": "GET",
              "case_type": "api",
              "category": "ERROR_HANDLING",
              "request_template": {"method": "GET", "path": "/non_existent_endpoint"},
              "assertions": [{"type": "status_code", "expected": 404}]
            },
            {
              "title": "documented headers",
              "endpoint": "/headers",
              "method": "GET",
              "case_type": "api",
              "category": "SMOKE",
              "request_template": {"method": "GET", "path": "/headers"},
              "assertions": [{"type": "status_code", "expected": 200}]
            }
          ],
          "ui_cases": []
        }
        ```
        """

    class FakePlanner:
        async def ainvoke(self, _messages):
            return FakePlannerMessage()

    class FakeDb:
        def add(self, _obj) -> None:
            return None

        async def flush(self) -> None:
            return None

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        def __init__(self, payload: dict) -> None:
            self._payload = payload
            self.text = json.dumps(payload)
            self.content = self.text.encode()

        def json(self) -> dict:
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            calls.append({"method": method, "url": url, **kwargs})
            assert "non_existent_endpoint" not in url
            return FakeResponse({"url": url, "headers": {}})

    async def fake_get_planner(_db):
        return FakePlanner()

    calls = []
    monkeypatch.setattr(tc_generator.llm_gateway, "get_planner", fake_get_planner)
    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(api_runner.settings, "API_MAX_EXECUTED_REQUESTS", 120)

    generated = await tc_generator.run(
        {
            "db_session": FakeDb(),
            "test_type": "api",
            "input_type": "swagger_json",
            "objective": "httpbin smoke",
            "target_url": "https://httpbin.org",
            "api_execution_policy": "safe_read_only",
            "parsed_api_schema": [
                {"method": "GET", "path": "/get", "response_status": "200"},
                {"method": "GET", "path": "/headers", "response_status": "200"},
            ],
            "api_plan": {"title": "API smoke"},
            "workflow_steps": [],
        }
    )
    executed = await api_runner.run(generated)
    reported = await reporter.run(executed)

    assert [call["url"] for call in calls] == [
        "https://httpbin.org/get",
        "https://httpbin.org/headers",
    ]
    assert len(generated["api_cases"]) == 2
    assert generated["api_cases"][0]["assertions"][1]["blocking"] is False
    assert any(
        item["kind"] == "out_of_scope_api_case"
        for item in generated["agent_case_diagnostics"]
    )
    assert any(
        item["kind"] == "unsupported_api_assertion"
        for item in generated["agent_case_diagnostics"]
    )
    assert executed["api_execution_result"]["failed"] == 0
    assert reported["final_report"]["overall_verdict"] == "PASS"
    assert reported["final_report"]["bugs_found"] == []
    assert reported["final_report"]["agent_diagnostics"]["case_diagnostics"]


@pytest.mark.asyncio
async def test_direct_api_url_builds_and_executes_smoke_request_without_schema(monkeypatch) -> None:
    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        def __init__(self, payload: dict) -> None:
            self._payload = payload
            self.text = json.dumps(payload)
            self.content = self.text.encode()

        def json(self) -> dict:
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            calls.append({"method": method, "url": url, **kwargs})
            return FakeResponse({"url": url})

    calls = []
    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)

    generated = await tc_generator.run(
        {
            "test_type": "api",
            "input_type": "url",
            "objective": "验证 GET 返回 200 且响应包含 url 字段",
            "target_url": "https://api.example.test/get",
            "api_execution_policy": "safe_read_only",
            "workflow_steps": [],
        }
    )
    executed = await api_runner.run(generated)

    assert generated["api_case_generation_source"] == "direct_url_fallback"
    assert generated["api_cases"][0]["request_template"] == {
        "method": "GET",
        "url": "https://api.example.test/get",
        "headers": {},
        "query_params": {},
        "body": None,
    }
    assert calls == [
        {
            "method": "GET",
            "url": "https://api.example.test/get",
            "headers": None,
            "json": None,
            "params": {},
        }
    ]
    assert executed["api_request_selection"]["source"] == "api_cases"
    assert executed["api_request_selection"]["selected_total"] == 1
    assert executed["api_execution_result"]["all_passed"] is True
    assert executed["execution_result"]["status_code"] == 0


@pytest.mark.asyncio
async def test_multi_direct_api_urls_flow_through_schema_case_generation(monkeypatch) -> None:
    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        def __init__(self, payload: dict) -> None:
            self._payload = payload
            self.text = json.dumps(payload)
            self.content = self.text.encode()

        def json(self) -> dict:
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            calls.append({"method": method, "url": url, **kwargs})
            payload = {"headers": {}} if url.endswith("/headers") else {"url": url, "headers": {}}
            return FakeResponse(payload)

    payload = normalize_planner_run_payload(
        None,
        [
            AgentPlanningMessage(
                session_id="test-session",
                role="user",
                content=(
                    "请测试 https://httpbin.org/get 和 https://httpbin.org/headers "
                    "两个只读接口，状态码必须是 200，响应 JSON 需要包含 url、headers 字段。"
                ),
            )
        ],
    )
    assert payload.source is not None
    calls = []
    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)

    loaded = await source_loader.run(
        {
            "source_input": payload.source,
            "input_type": "unknown",
            "test_type": "api",
            "objective": payload.objective,
            "api_execution_policy": payload.api_execution_policy,
            "workflow_steps": [],
        }
    )
    generated = await tc_generator.run(loaded)
    executed = await api_runner.run(generated)

    assert loaded["target_url"] == "https://httpbin.org"
    assert [(item["method"], item["path"]) for item in loaded["parsed_api_schema"]] == [
        ("GET", "/get"),
        ("GET", "/headers"),
    ]
    assert [case["endpoint"] for case in generated["api_cases"]] == ["/get", "/headers"]
    assert [call["url"] for call in calls] == [
        "https://httpbin.org/get",
        "https://httpbin.org/headers",
    ]
    assert executed["api_request_selection"]["selected_total"] == 2
    assert executed["api_execution_result"]["executed"] == 2
    assert executed["api_execution_result"]["passed"] == 1
    assert executed["api_execution_result"]["failed"] == 1
    assert executed["api_execution_result"]["all_passed"] is False
    headers_result = executed["api_execution_result"]["results"][1]
    schema_assertion = headers_result["assertion_results"][1]
    assert schema_assertion["type"] == "schema"
    assert schema_assertion["blocking"] is True
    assert schema_assertion["passed"] is False


@pytest.mark.asyncio
async def test_generated_root_meta_json_path_assertion_is_advisory_without_schema(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}
        text = '{"ok": true}'
        content = text.encode()

        def json(self) -> dict:
            return {"ok": True}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            calls.append({"method": method, "url": url, **kwargs})
            assert "/missing" not in url
            return FakeResponse()

    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(api_runner.settings, "API_MAX_EXECUTED_REQUESTS", 120)

    executed = await api_runner.run(
        {
            "test_type": "api",
            "target_url": "https://api.example.test",
            "objective": "Verify agent worker session planning stability without inventing product fields.",
            "api_cases_generated": True,
            "api_execution_policy": "safe_read_only",
            "parsed_api_schema": [
                {"method": "GET", "path": "/health", "response_status": "200"},
            ],
            "api_cases": [
                {
                    "title": "health response shape",
                    "request_template": {"method": "GET", "path": "/health"},
                    "assertions": [
                        {"type": "status_code", "expected": 200},
                        {
                            "type": "json_path",
                            "path": "$",
                            "operator": "equals",
                            "expected": "is_object",
                        },
                    ],
                },
                {
                    "title": "invented stability endpoint",
                    "request_template": {"method": "GET", "path": "/missing"},
                    "assertions": [{"type": "status_code", "expected": 200}],
                },
            ],
            "workflow_steps": [],
        }
    )
    reported = await reporter.run(executed)

    api_result = reported["api_execution_result"]
    assertion_results = api_result["results"][0]["assertion_results"]

    assert [call["url"] for call in calls] == ["https://api.example.test/health"]
    assert api_result["passed"] == 1
    assert api_result["failed"] == 0
    assert any(
        item["type"] == "json_path"
        and item["path"] == "$"
        and item["blocking"] is False
        and item["advisory"] is True
        and item["passed"] is False
        for item in assertion_results
    )
    assert any(
        item["kind"] == "unsupported_api_assertion" and item["action"] == "downgraded"
        for item in reported["agent_case_diagnostics"]
    )
    assert any(
        item["kind"] == "out_of_scope_api_case" and item["action"] == "dropped"
        for item in reported["agent_case_diagnostics"]
    )
    assert reported["final_report"]["overall_verdict"] == "PASS"
    assert reported["final_report"]["bugs_found"] == []
    assert determine_final_status(reported) == TaskStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_generated_grounded_json_path_assertion_still_blocks_when_false(monkeypatch) -> None:
    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}
        text = '{"user": {"name": "Grace"}}'
        content = text.encode()

        def json(self) -> dict:
            return {"user": {"name": "Grace"}}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(api_runner.settings, "API_MAX_EXECUTED_REQUESTS", 120)

    executed = await api_runner.run(
        {
            "test_type": "api",
            "target_url": "https://api.example.test",
            "objective": "Verify profile user name is Ada.",
            "api_cases_generated": True,
            "parsed_api_schema": [
                {
                    "method": "GET",
                    "path": "/profile",
                    "response_status": "200",
                    "response_schema": {
                        "type": "object",
                        "properties": {
                            "user": {
                                "type": "object",
                                "properties": {"name": {"type": "string"}},
                            }
                        },
                    },
                },
            ],
            "api_cases": [
                {
                    "title": "profile contract",
                    "request_template": {"method": "GET", "path": "/profile"},
                    "assertions": [
                        {"type": "status_code", "expected": 200},
                        {"type": "json_path", "path": "$.user.name", "expected": "Ada"},
                    ],
                }
            ],
            "workflow_steps": [],
        }
    )
    reported = await reporter.run(executed)

    api_result = reported["api_execution_result"]
    assertion_results = api_result["results"][0]["assertion_results"]

    assert api_result["passed"] == 0
    assert api_result["failed"] == 1
    assert any(
        item["type"] == "json_path"
        and item["path"] == "$.user.name"
        and item["blocking"] is True
        and item["passed"] is False
        for item in assertion_results
    )
    assert reported["final_report"]["overall_verdict"] == "FAIL"
    assert reported["final_report"]["bugs_found"]
    assert determine_final_status(reported) == TaskStatus.FAILED


@pytest.mark.asyncio
async def test_generated_objective_grounded_session_assertion_still_blocks(monkeypatch) -> None:
    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}
        text = '{"session": {"status": "inactive"}}'
        content = text.encode()

        def json(self) -> dict:
            return {"session": {"status": "inactive"}}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(api_runner.settings, "API_MAX_EXECUTED_REQUESTS", 120)

    executed = await api_runner.run(
        {
            "test_type": "api",
            "target_url": "https://api.example.test",
            "objective": "Verify login session status is active.",
            "api_cases_generated": True,
            "api_execution_policy": "safe_read_only",
            "parsed_api_schema": [
                {"method": "GET", "path": "/session", "response_status": "200"},
            ],
            "api_cases": [
                {
                    "title": "session status contract",
                    "request_template": {"method": "GET", "path": "/session"},
                    "assertions": [
                        {"type": "status_code", "expected": 200},
                        {"type": "json_path", "path": "$.session.status", "expected": "active"},
                    ],
                }
            ],
            "workflow_steps": [],
        }
    )

    api_result = executed["api_execution_result"]
    assertion_results = api_result["results"][0]["assertion_results"]

    assert api_result["passed"] == 0
    assert api_result["failed"] == 1
    assert any(
        item["type"] == "json_path"
        and item["path"] == "$.session.status"
        and item["blocking"] is True
        and item["passed"] is False
        and not item.get("advisory")
        for item in assertion_results
    )
    assert executed.get("agent_case_diagnostics") in (None, [])


@pytest.mark.asyncio
async def test_generated_objective_grounded_camel_case_assertion_still_blocks(
    monkeypatch,
) -> None:
    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}
        text = '{"userName": "Grace"}'
        content = text.encode()

        def json(self) -> dict:
            return {"userName": "Grace"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(api_runner.settings, "API_MAX_EXECUTED_REQUESTS", 120)

    executed = await api_runner.run(
        {
            "test_type": "api",
            "target_url": "https://api.example.test",
            "objective": "Verify user name is Ada.",
            "api_cases_generated": True,
            "api_execution_policy": "safe_read_only",
            "parsed_api_schema": [
                {"method": "GET", "path": "/profile", "response_status": "200"},
            ],
            "api_cases": [
                {
                    "title": "profile user name contract",
                    "request_template": {"method": "GET", "path": "/profile"},
                    "assertions": [
                        {"type": "status_code", "expected": 200},
                        {"type": "json_path", "path": "$.userName", "expected": "Ada"},
                    ],
                }
            ],
            "workflow_steps": [],
        }
    )

    api_result = executed["api_execution_result"]
    assertion_results = api_result["results"][0]["assertion_results"]

    assert api_result["passed"] == 0
    assert api_result["failed"] == 1
    assert any(
        item["type"] == "json_path"
        and item["path"] == "$.userName"
        and item["blocking"] is True
        and item["passed"] is False
        and not item.get("advisory")
        for item in assertion_results
    )
    assert executed.get("agent_case_diagnostics") in (None, [])


@pytest.mark.asyncio
async def test_embedding_service_redacts_text_before_provider() -> None:
    class FakeClient:
        embedded_texts: list[str]

        async def aembed_documents(self, texts):
            self.embedded_texts = texts
            return [[1.0, 0.0]]

    client = FakeClient()
    vectors = await EmbeddingService().embed_documents_with_client(
        client,
        ["Checkout failed with password=secret-token"],
    )

    assert vectors == [[1.0, 0.0]]
    assert "secret-token" not in client.embedded_texts[0]
    assert "[REDACTED]" in client.embedded_texts[0]


@pytest.mark.asyncio
async def test_embedding_service_uses_local_vector_fallback_when_provider_errors() -> None:
    class FailingClient:
        async def aembed_documents(self, _texts):
            raise RuntimeError("404 page not found")

        async def aembed_query(self, _text):
            raise RuntimeError("404 page not found")

    service = EmbeddingService()

    document_vectors = await service.embed_documents_with_client(
        FailingClient(),
        ["Checkout regression with 500 error"],
    )
    query_vector = await service.embed_query_with_client(
        FailingClient(),
        "Checkout regression",
    )

    assert len(document_vectors) == 1
    assert len(document_vectors[0]) == len(query_vector)
    assert any(value != 0 for value in document_vectors[0])
    assert any(value != 0 for value in query_vector)


def test_llm_gateway_builds_openai_embeddings_client(monkeypatch) -> None:
    monkeypatch.setattr("app.core.llm_gateway.decrypt_value", lambda _value: "sk-test")
    provider = LLMProvider(
        name="OpenAI-compatible",
        type=ProviderType.OPENAI,
        base_url="https://llm.example.test/v1",
        api_key_encrypted="encrypted",
        model_name="gpt-4o",
    )

    client = LLMGateway().build_embeddings_client(provider)

    assert isinstance(client, OpenAIEmbeddings)
    assert client.model == "text-embedding-3-small"
    assert client.openai_api_base == "https://llm.example.test/v1"


@pytest.mark.asyncio
async def test_knowledge_service_stores_embedding_for_new_entries(monkeypatch) -> None:
    class FakeEmbeddingService:
        calls: list[tuple[object, str]]

        def __init__(self) -> None:
            self.calls = []

        async def embed_document(self, db, text):
            self.calls.append((db, text))
            return [0.25, 0.75]

    class FakeDb:
        added: object | None = None
        committed = False
        refreshed: object | None = None

        def add(self, entry) -> None:
            self.added = entry

        async def commit(self) -> None:
            self.committed = True

        async def refresh(self, entry) -> None:
            self.refreshed = entry

    fake_embeddings = FakeEmbeddingService()
    monkeypatch.setattr("app.services.knowledge_service.embedding_service", fake_embeddings)
    db = FakeDb()

    entry = await KnowledgeService().create(
        db,
        content="Checkout failure with token=secret-token",
        source_script_id="run-1",
    )

    assert fake_embeddings.calls == [(db, "Checkout failure with token=secret-token")]
    assert db.added is entry
    assert db.committed is True
    assert db.refreshed is entry
    assert entry.embedding == [0.25, 0.75]
    assert entry.source_script_id == "run-1"


@pytest.mark.asyncio
async def test_knowledge_sink_generates_redacted_memory_candidate_from_evaluation(monkeypatch) -> None:
    created: list[dict] = []

    async def fake_create(db, *, content: str, source_script_id: str | None = None):
        created.append({"db": db, "content": content, "source_script_id": source_script_id})
        return SimpleNamespace(id="knowledge-1", content=content, source_script_id=source_script_id)

    db = object()
    monkeypatch.setattr(knowledge_sink.knowledge_service, "create", fake_create)

    state = await knowledge_sink.run(
        {
            "db_session": db,
            "task_id": "run-memory-1",
            "objective": "Checkout regression password=secret-password",
            "target_url": "https://shop.example.test/checkout?token=secret-token",
            "source_input": "https://shop.example.test/checkout?token=secret-token",
            "test_type": "ui",
            "input_type": "url",
            "workflow_steps": [],
            "evidence_evaluation": {
                "stage": "ui",
                "confidence": "high",
                "sufficient_evidence": False,
                "next_action": "ask_human",
                "failure_type": "auth_failure",
                "reason": "Login failed with password=secret-password",
                "human_question": "Please provide a fresh password=secret-password",
                "missing_evidence": ["valid session cookie=session-secret"],
                "replan_hint": "Collect fresh login context before replaying checkout.",
            },
            "agent_observations": [
                {
                    "observation_id": "obs-1",
                    "stage": "ui_runner",
                    "layer": "ui",
                    "tool_name": "ui.playwright_cli",
                    "status": "failed",
                    "outcome": "blocked",
                    "failure_type": "auth_failure",
                    "summary": "Login failed with token=secret-token",
                    "evidence_ids": ["evidence-1"],
                }
            ],
            "agent_protocol_summary": {
                "observation_total": 1,
                "evidence_total": 1,
                "by_failure_type": {"auth_failure": 1},
            },
            "final_report": {
                "overall_verdict": "FAIL",
                "summary": "Auth setup blocked checkout.",
                "recommendations": ["Refresh login context before replay."],
            },
        }
    )

    assert "bug_report" not in state
    candidate = state["memory_candidates"][0]
    serialized = json.dumps(candidate, ensure_ascii=False)
    assert candidate["schema_version"] == knowledge_sink.MEMORY_CANDIDATE_SCHEMA
    assert candidate["kind"] == "known_blocker"
    assert candidate["confidence"] == "high"
    assert candidate["failure_type"] == "auth_failure"
    assert candidate["facts"][0]["fact_type"] == "known_blocker"
    assert candidate["observation_refs"][0]["observation_id"] == "obs-1"
    assert "secret-password" not in serialized
    assert "secret-token" not in serialized
    assert "session-secret" not in serialized
    assert created[0]["db"] is db
    assert created[0]["source_script_id"] == "run-memory-1"
    assert knowledge_sink.MEMORY_CANDIDATE_MARKER in created[0]["content"]
    assert "secret-password" not in created[0]["content"]
    assert "secret-token" not in created[0]["content"]


@pytest.mark.asyncio
async def test_knowledge_retriever_uses_vector_similarity_and_redacts_context(monkeypatch) -> None:
    class FakeEmbeddingService:
        async def get_client(self, _db):
            return object()

        async def embed_query_with_client(self, _client, _query):
            return [1.0, 0.0]

        async def embed_documents_with_client(self, _client, _texts):
            raise AssertionError("stored embeddings should be used")

    class FakeResult:
        def scalars(self):
            return [
                SimpleNamespace(
                    id="knowledge-1",
                    source_script_id="run-1",
                    content="Checkout failure root cause password=secret-token",
                    embedding=[1.0, 0.0],
                    created_at=datetime(2026, 5, 23),
                ),
                SimpleNamespace(
                    id="knowledge-2",
                    source_script_id=None,
                    content="Unrelated profile note",
                    embedding=[0.0, 1.0],
                    created_at=datetime(2026, 5, 22),
                ),
            ]

    class FakeDb:
        async def execute(self, _stmt):
            return FakeResult()

    monkeypatch.setattr(knowledge_retriever, "embedding_service", FakeEmbeddingService())

    state = await knowledge_retriever.run(
        {
            "db_session": FakeDb(),
            "objective": "checkout regression",
            "target_url": "https://shop.example.test/checkout",
            "source_input": "https://shop.example.test/checkout",
            "test_type": "ui",
            "input_type": "url",
            "workflow_steps": [],
        }
    )

    assert state["rag_retrieval"]["status"] == "matched"
    assert state["rag_retrieval"]["mode"] == "vector"
    assert state["rag_retrieval"]["backend"] == "database"
    assert state["rag_retrieval"]["vector_source_count"] == 2
    assert state["rag_retrieval"]["sources"][0]["id"] == "knowledge-1"
    assert state["rag_retrieval"]["sources"][0]["mode"] == "vector"
    assert "secret-token" not in state["rag_context"]
    assert "[REDACTED]" in state["rag_context"]
    assert "rag-knowledge-retrieval" in {skill["name"] for skill in state["skill_plan"]}


@pytest.mark.asyncio
async def test_knowledge_retriever_returns_structured_memory_facts(monkeypatch) -> None:
    candidate = {
        "schema_version": knowledge_sink.MEMORY_CANDIDATE_SCHEMA,
        "kind": "known_blocker",
        "confidence": "high",
        "source": "execution_evaluation",
        "source_run_id": "previous-run-1",
        "target_hint": "https://shop.example.test/checkout?token=secret-token",
        "objective": "Checkout regression",
        "test_type": "ui",
        "stage": "ui",
        "next_action": "ask_human",
        "sufficient_evidence": False,
        "failure_type": "auth_failure",
        "reason": "Checkout login is blocked.",
        "planner_hint": "Collect fresh login context with password=secret-password before replay.",
        "facts": [
            {
                "fact_type": "known_blocker",
                "summary": "Checkout login is blocked.",
                "failure_type": "auth_failure",
                "next_action": "ask_human",
                "planner_hint": "Collect fresh login context with password=secret-password before replay.",
            }
        ],
        "observation_refs": [{"observation_id": "obs-1", "evidence_ids": ["evidence-1"]}],
    }

    class FakeEmbeddingService:
        async def get_client(self, _db):
            return object()

        async def embed_query_with_client(self, _client, _query):
            return [1.0, 0.0]

        async def embed_documents_with_client(self, _client, _texts):
            raise AssertionError("stored embeddings should be used")

    class FakeResult:
        def scalars(self):
            return [
                SimpleNamespace(
                    id="knowledge-structured",
                    source_script_id="previous-run-1",
                    content=f"{knowledge_sink.MEMORY_CANDIDATE_MARKER}\n{json.dumps(candidate, ensure_ascii=False)}",
                    embedding=[1.0, 0.0],
                    created_at=datetime(2026, 5, 24),
                )
            ]

    class FakeDb:
        async def execute(self, _stmt):
            return FakeResult()

    monkeypatch.setattr(knowledge_retriever, "embedding_service", FakeEmbeddingService())

    state = await knowledge_retriever.run(
        {
            "db_session": FakeDb(),
            "objective": "checkout regression",
            "target_url": "https://shop.example.test/checkout",
            "source_input": "https://shop.example.test/checkout",
            "test_type": "ui",
            "input_type": "url",
            "workflow_steps": [],
        }
    )

    fact = state["rag_facts"][0]
    serialized = json.dumps(state["rag_retrieval"], ensure_ascii=False)
    assert state["rag_retrieval"]["fact_count"] == 1
    assert fact["fact_type"] == "known_blocker"
    assert fact["failure_type"] == "auth_failure"
    assert fact["source_script_id"] == "previous-run-1"
    assert "Structured memory facts" in state["rag_context"]
    assert "secret-token" not in serialized
    assert "secret-password" not in serialized
    assert "[REDACTED]" in serialized


@pytest.mark.asyncio
async def test_knowledge_retriever_marks_lexical_fallback_when_embeddings_unavailable(
    monkeypatch,
) -> None:
    class FakeEmbeddingService:
        async def get_client(self, _db):
            raise EmbeddingUnavailableError("No active OpenAI-compatible embedding provider configured")

    class FakeResult:
        def scalars(self):
            return [
                SimpleNamespace(
                    id="knowledge-1",
                    source_script_id="run-1",
                    content="Checkout regression failed on cart total",
                    embedding=None,
                    created_at=datetime(2026, 5, 23),
                )
            ]

    class FakeDb:
        async def execute(self, _stmt):
            return FakeResult()

    monkeypatch.setattr(knowledge_retriever, "embedding_service", FakeEmbeddingService())

    state = await knowledge_retriever.run(
        {
            "db_session": FakeDb(),
            "objective": "checkout regression",
            "target_url": "https://shop.example.test/checkout",
            "source_input": "https://shop.example.test/checkout",
            "test_type": "ui",
            "input_type": "url",
            "workflow_steps": [],
        }
    )

    assert state["rag_retrieval"]["status"] == "fallback_lexical"
    assert state["rag_retrieval"]["mode"] == "lexical_fallback"
    assert state["rag_retrieval"]["vector_source_count"] == 0
    assert "embedding provider" in state["rag_retrieval"]["fallback_reason"]
    assert state["rag_retrieval"]["sources"][0]["mode"] == "lexical_fallback"


def test_ui_runner_adds_smart_waits_after_actions() -> None:
    batches = _build_ui_case_batches(
        [
            {
                "title": "click flow",
                "playwright_commands": ["open https://web.test", "click \"Submit\"", "snapshot"],
            }
        ],
        "https://web.test",
    )

    commands = [spec["command"] for spec in batches[0]["commands"] if not spec.get("skip")]
    click_index = commands.index("click \"Submit\"")

    assert any(command.startswith("run-code") for command in commands)
    assert click_index < next(
        index for index, command in enumerate(commands[click_index + 1:], start=click_index + 1)
        if command.startswith("run-code")
    )


def test_playwright_skill_compiles_structured_actions_and_blocks_run_code() -> None:
    specs = compile_playwright_ui_actions(
        [
            {"type": "open", "url": "https://web.test"},
            {"type": "click_ref", "ref": "e5", "reason": "Open menu"},
            {"type": "fill_ref", "ref": "e6", "value": "admin"},
            {"type": "assert_visible", "text": "Dashboard"},
            {"type": "screenshot"},
            {"type": "run_code", "code": "async page => await page.click('button')"},
        ],
        target_url="https://fallback.test",
    )

    assert [spec["agent_action_type"] for spec in specs] == [
        "open",
        "click_ref",
        "fill_ref",
        "assert_visible",
        "screenshot",
        "run_code",
    ]
    assert [spec["command"] for spec in specs[:5]] == [
        "open https://web.test",
        "click e5",
        "fill e6 admin",
        "snapshot",
        "screenshot",
    ]
    assert specs[3]["kind"] == "assert_snapshot_contains"
    assert specs[3]["expected"] == "Dashboard"
    assert specs[4]["kind"] == "screenshot"
    assert specs[5]["skip"] is True
    assert specs[5]["blocked"] is True
    assert specs[5]["risk"] == "high_risk"
    assert "Blocked arbitrary code execution" in specs[5]["normalization"]


def test_ui_runner_builds_structured_playwright_skill_batches() -> None:
    batches = _build_ui_case_batches(
        [
            {
                "title": "structured checkout",
                "ui_actions": [
                    {"type": "open"},
                    {"type": "click_ref", "ref": "e2", "reason": "Open checkout"},
                    {"type": "assert_visible", "text": "Checkout"},
                    {"type": "run_code", "code": "async page => await page.click('button')"},
                ],
            }
        ],
        "https://web.test/checkout",
    )

    specs = batches[0]["commands"]
    executable = [spec for spec in specs if not spec.get("skip")]
    blocked = [spec for spec in specs if spec.get("blocked")]

    assert executable[0]["command"] == "open https://web.test/checkout"
    assert executable[0]["agent_action_type"] == "open"
    assert executable[0]["transport"] == "playwright-cli"
    assert any(
        spec["command"] == "click e2"
        and spec["agent_action_type"] == "click_ref"
        and spec["agent_action"]["reason"] == "Open checkout"
        for spec in executable
    )
    assert any(
        spec["kind"] == "assert_snapshot_contains"
        and spec["agent_action_type"] == "assert_visible"
        and spec["expected"] == "Checkout"
        for spec in executable
    )
    assert blocked[0]["agent_action_type"] == "run_code"
    assert blocked[0]["risk"] == "high_risk"
    assert any(spec.get("kind") == "screenshot" for spec in specs)


def test_agent_execution_protocol_preserves_structured_ui_action_metadata() -> None:
    state: dict = {}

    observations = append_ui_result_observations(
        state,
        {
            "commands": [
                {
                    "case_index": 0,
                    "case_title": "structured checkout",
                    "command": "structured click_ref: Open checkout",
                    "normalized_command": "click e2",
                    "status": "executed",
                    "status_code": 0,
                    "stdout": "",
                    "stderr": "",
                    "passed": True,
                    "agent_action": {"type": "click_ref", "ref": "e2", "reason": "Open checkout"},
                    "agent_action_type": "click_ref",
                    "transport": "playwright-cli",
                    "risk": "safe_ui_action",
                },
                {
                    "case_index": 0,
                    "case_title": "structured checkout",
                    "command": "structured run_code",
                    "normalized_command": None,
                    "status": "blocked",
                    "status_code": 1,
                    "stdout": "",
                    "stderr": "Blocked arbitrary code execution in structured Playwright action.",
                    "passed": False,
                    "agent_action": {"type": "run_code"},
                    "agent_action_type": "run_code",
                    "transport": "playwright-cli",
                    "risk": "high_risk",
                },
            ]
        },
        stage="ui_runner",
    )

    assert observations[0]["status"] == "success"
    assert observations[0]["inputs"]["agent_action_type"] == "click_ref"
    assert observations[0]["metadata"]["agent_action"]["reason"] == "Open checkout"
    assert observations[1]["status"] == "blocked"
    assert observations[1]["failure_type"] == "ui_high_risk_action_blocked"
    assert observations[1]["outcome"] == "blocked"
    assert state["agent_protocol_summary"]["by_failure_type"]["ui_high_risk_action_blocked"] == 1


def test_ui_runner_restores_authenticated_context_for_legacy_business_cases() -> None:
    batches = _build_ui_case_batches(
        [
            {
                "title": "legacy menu case",
                "playwright_commands": ["open https://web.test/login", "click \"商品管理\"", "snapshot"],
            }
        ],
        "https://web.test/login",
        authenticated_setup_commands=["open about:blank", 'state-load "auth.json"', "goto https://web.test/admin"],
    )

    commands = [spec["command"] for spec in batches[0]["commands"] if not spec.get("skip")]

    assert commands[:3] == ["open about:blank", 'state-load "auth.json"', "goto https://web.test/admin"]
    assert "open https://web.test/login" not in commands
    assert "click \"商品管理\"" in commands


def test_ui_runner_keeps_login_validation_cases_on_login_page() -> None:
    batches = _build_ui_case_batches(
        [
            {
                "title": "wrong password login failed",
                "category": "AUTH",
                "playwright_commands": [
                    "open https://web.test/login",
                    "fill e1 \"admin\"",
                    "fill e2 \"wrong\"",
                    "click e3",
                    "snapshot",
                ],
            }
        ],
        "https://web.test/login",
        authenticated_setup_commands=["open about:blank", 'state-load "auth.json"', "goto https://web.test/app"],
    )

    commands = [spec["command"] for spec in batches[0]["commands"] if not spec.get("skip")]

    assert commands[0] == "open https://web.test/login"
    assert 'state-load "auth.json"' not in commands
    assert "fill e1 \"admin\"" in commands


def test_ui_runner_strips_legacy_login_steps_for_generic_authenticated_cases() -> None:
    batches = _build_ui_case_batches(
        [
            {
                "title": "Primary workflow after setup",
                "category": "INTERACTION",
                "playwright_commands": [
                    "open https://web.test/login",
                    "fill e1 \"admin\"",
                    "fill e2 \"secret\"",
                    "click e3",
                    "click \"Open workspace\"",
                    "snapshot",
                ],
            }
        ],
        "https://web.test/login",
        authenticated_setup_commands=["open about:blank", 'state-load "auth.json"', "goto https://web.test/app"],
    )

    commands = [spec["command"] for spec in batches[0]["commands"] if not spec.get("skip")]

    assert commands[:3] == ["open about:blank", 'state-load "auth.json"', "goto https://web.test/app"]
    assert "open https://web.test/login" not in commands
    assert "fill e1 \"admin\"" not in commands
    assert "click \"Open workspace\"" in commands


@pytest.mark.asyncio
async def test_api_runner_redacts_secret_response_values_from_legacy_execution_result(monkeypatch) -> None:
    class FakeResponse:
        status_code = 200
        text = ""

        def json(self) -> dict:
            return {
                "access_token": "response-token-secret",
                "profile": {"password": "response-password-secret"},
                "name": "Ada",
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)

    result = await api_runner.run(
        {
            "test_type": "api",
            "target_url": "https://api.example.test",
            "api_cases": [
                {
                    "title": "token issuing endpoint",
                    "request_template": {"method": "GET", "path": "/token"},
                }
            ],
            "workflow_steps": [],
        }
    )

    legacy_result = result["execution_result"]
    dumped = str(legacy_result)

    assert "response-token-secret" not in dumped
    assert "response-password-secret" not in dumped
    assert legacy_result["api_results"][0]["body"]["access_token"] == REDACTED_VALUE
    assert legacy_result["api_results"][0]["body"]["profile"]["password"] == REDACTED_VALUE
    observation = result["agent_observations"][0]
    assert observation["tool_name"] == "api.http_request"
    assert observation["status"] == "success"
    assert observation["inputs"]["method"] == "GET"
    assert observation["inputs"]["path"] == "/token"
    assert observation["outputs"]["status_code"] == 200
    assert observation["outputs"]["duration_ms"] is not None
    assert observation["outputs"]["assertions"][0]["type"] == "status_code"
    assert observation["outputs"]["error_type"] is None
    assert observation["outputs"]["failure_type"] is None
    assert observation["outputs"]["safety_decision"] == "safe_read_executed"
    assert observation["outputs"]["evidence_refs"] == observation["evidence_ids"]
    assert observation["evidence_ids"]
    assert result["agent_protocol_summary"]["by_layer"]["api"] == 1
    assert "response-token-secret" not in json.dumps(result["agent_evidence"], ensure_ascii=False)


@pytest.mark.asyncio
async def test_api_runner_extracts_and_injects_dependencies_with_tool_calls(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        def __init__(self, payload: dict, status_code: int = 200) -> None:
            self._payload = payload
            self.status_code = status_code
            self.text = ""

        def json(self) -> dict:
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            calls.append({"method": method, "url": url, **kwargs})
            if url.endswith("/login"):
                return FakeResponse({"token": "chain-token"})
            return FakeResponse({"user": {"name": "Ada"}})

    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)

    result = await api_runner.run(
        {
            "test_type": "api",
            "target_url": "https://api.example.test",
            "api_execution_policy": "write_allowed",
            "api_cases": [
                {
                    "title": "login",
                    "request_template": {
                        "method": "POST",
                        "path": "/login",
                        "body": {"username": "admin", "password": "secret"},
                    },
                    "assertions": [{"type": "json_path", "path": "$.token", "expected": "not_null"}],
                    "extract": {"token": "$.token"},
                },
                {
                    "title": "profile",
                    "request_template": {
                        "method": "GET",
                        "path": "/me",
                        "headers": {"Authorization": "Bearer {{token}}"},
                    },
                    "depends_on": ["token"],
                    "response_schema": {
                        "type": "object",
                        "required": ["user"],
                        "properties": {"user": {"type": "object"}},
                    },
                    "assertions": [
                        {"type": "json_path", "path": "$.user.name", "expected": "Ada"},
                        {"type": "schema", "blocking": True},
                    ],
                },
            ],
            "workflow_steps": [],
        }
    )

    api_result = result["api_execution_result"]
    tool_names = {call["tool"] for call in result["tool_calls"]}

    assert api_result["passed"] == 2
    assert api_result["failed"] == 0
    assert calls[1]["headers"]["Authorization"] == "Bearer chain-token"
    assert {"api.http_request", "api.extract_value", "api.inject_dependency", "api.schema_assert"} <= tool_names
    assert result["agent_protocol_summary"]["by_layer"]["api"] == 2
    assert {observation["status"] for observation in result["agent_observations"]} == {"success"}
    profile_observation = result["agent_observations"][1]
    assert profile_observation["inputs"]["method"] == "GET"
    assert profile_observation["inputs"]["path"] == "/me"
    assert profile_observation["outputs"]["assertion_count"] >= 3
    assert {
        assertion["type"] for assertion in profile_observation["outputs"]["assertions"]
    } >= {"status_code", "json_path", "schema"}
    assert profile_observation["outputs"]["safety_decision"] == "safe_read_executed"
    assert profile_observation["outputs"]["evidence_refs"] == profile_observation["evidence_ids"]
    assert {
        evidence["kind"] for evidence in result["agent_evidence"]
    } >= {"api_response", "api_assertion"}


@pytest.mark.asyncio
async def test_api_runner_derives_schema_path_param_from_prior_collection_response(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        def __init__(self, payload: dict) -> None:
            self._payload = payload
            self.status_code = 200
            self.text = json.dumps(payload)
            self.headers = {"content-type": "application/json"}
            self.content = self.text.encode()

        def json(self) -> dict:
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            calls.append({"method": method, "url": url, **kwargs})
            if url.endswith("/orders"):
                return FakeResponse({"rows": [{"id": 42, "name": "SO-42"}]})
            return FakeResponse({"id": 42, "name": "SO-42"})

    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(api_runner.settings, "API_MAX_EXECUTED_REQUESTS", 120)

    result = await api_runner.run(
        {
            "test_type": "api",
            "target_url": "https://api.example.test",
            "api_execution_policy": "safe_read_only",
            "parsed_api_schema": [
                {
                    "method": "GET",
                    "path": "/orders/{id}",
                    "path_params": [{"name": "id", "schema": {"type": "integer"}}],
                    "response_status": "200",
                },
                {"method": "GET", "path": "/orders", "response_status": "200"},
            ],
            "workflow_steps": [],
        }
    )

    api_result = result["api_execution_result"]
    tool_names = {call["tool"] for call in result["tool_calls"]}

    assert [call["url"] for call in calls] == [
        "https://api.example.test/orders",
        "https://api.example.test/orders/42",
    ]
    assert api_result["executed"] == 2
    assert api_result["skipped"] == 0
    assert api_result["passed"] == 2
    assert "https://api.example.test/orders/1" not in [call["url"] for call in calls]
    assert {"api.extract_value", "api.inject_dependency"} <= tool_names


@pytest.mark.asyncio
async def test_api_runner_skips_schema_path_param_when_no_prior_value(monkeypatch) -> None:
    calls = []

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs):
            calls.append({"method": method, "url": url, **kwargs})
            raise AssertionError("path-param endpoint should be skipped before HTTP execution")

    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(api_runner.settings, "API_MAX_EXECUTED_REQUESTS", 120)

    result = await api_runner.run(
        {
            "test_type": "api",
            "target_url": "https://api.example.test",
            "api_execution_policy": "safe_read_only",
            "parsed_api_schema": [
                {
                    "method": "GET",
                    "path": "/orders/{id}",
                    "path_params": [{"name": "id", "schema": {"type": "integer"}}],
                    "response_status": "200",
                }
            ],
            "workflow_steps": [],
        }
    )

    api_result = result["api_execution_result"]
    skipped = api_result["results"][0]

    assert calls == []
    assert api_result["total"] == 1
    assert api_result["executed"] == 0
    assert api_result["skipped"] == 1
    assert api_result["failed"] == 0
    assert api_result["http_executed"] == 0
    assert skipped["skip_type"] == api_runner.PATH_PARAM_UNRESOLVED_SKIP_TYPE
    assert "合成占位值" in skipped["skip_reason"]
    assert "/orders/1" not in skipped["url"]


@pytest.mark.asyncio
async def test_api_runner_write_allowed_blocks_mutating_curated_case(monkeypatch) -> None:
    calls = []

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs):
            calls.append({"method": method, "url": url, **kwargs})
            raise AssertionError("curated mutating write should be blocked before HTTP execution")

    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)

    result = await api_runner.run(
        {
            "test_type": "api",
            "target_url": "https://api.example.test",
            "api_execution_policy": "write_allowed",
            "api_cases": [
                {
                    "title": "create item",
                    "request_template": {
                        "method": "POST",
                        "path": "/items",
                        "body": {"name": "TestClaw temporary item"},
                    },
                }
            ],
            "workflow_steps": [],
        }
    )

    api_result = result["api_execution_result"]

    assert calls == []
    assert api_result["executed"] == 0
    assert api_result["skipped"] == 1
    assert api_result["results"][0]["skip_type"] == api_runner.SAFE_WRITE_BLOCK_SKIP_TYPE
    observation = result["agent_observations"][0]
    assert observation["failure_type"] == "safe_write_blocked"
    assert observation["outputs"]["skip_type"] == api_runner.SAFE_WRITE_BLOCK_SKIP_TYPE
    assert observation["outputs"]["raw_failure_type"] == api_runner.SAFE_WRITE_BLOCK_SKIP_TYPE
    assert observation["outputs"]["safety_decision"] == "blocked_by_safe_write_gate"
    assert observation["outputs"]["evidence_refs"] == observation["evidence_ids"]


@pytest.mark.asyncio
async def test_api_runner_auth_negative_case_assertion_strips_default_auth_header(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        status_code = 200
        text = '{"code": 500, "msg": "unauthenticated"}'
        headers = {"content-type": "application/json"}
        content = text.encode()

        def json(self) -> dict:
            return {"code": 500, "msg": "unauthenticated"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            calls.append({"method": method, "url": url, **kwargs})
            return FakeResponse()

    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)

    result = await api_runner.run(
        {
            "test_type": "api",
            "target_url": "https://api.example.test",
            "api_execution_policy": "safe_read_only",
            "auth_headers": {"Authorization": "Bearer real-token"},
            "api_cases": [
                {
                    "title": "缺少Token访问受限资源",
                    "category": "AUTH",
                    "request_template": {"method": "GET", "path": "/private"},
                    "assertions": [{"type": "status_code", "expected": 401}],
                }
            ],
            "workflow_steps": [],
        }
    )

    assert calls[0].get("headers") in (None, {})
    api_result = result["api_execution_result"]
    assert api_result["failed"] == 0
    assert api_result["advisory"] == 1
    assert api_result["results"][0]["skip_type"] == "auth_advisory"


@pytest.mark.asyncio
async def test_api_runner_generates_mock_body_for_authenticated_write_schema(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        def __init__(self, payload: dict, status_code: int) -> None:
            self._payload = payload
            self.status_code = status_code
            self.text = ""

        def json(self) -> dict:
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            calls.append({"method": method, "url": url, **kwargs})
            headers = kwargs.get("headers") or {}
            body = kwargs.get("json")
            if not headers.get("Authorization"):
                return FakeResponse({"code": 401}, 401)
            if not isinstance(body, dict) or not body:
                return FakeResponse({"code": 400}, 400)
            if not isinstance(body.get("email"), str) or "@" not in body["email"]:
                return FakeResponse({"code": 422}, 422)
            if not isinstance(body.get("password"), str) or not body["password"]:
                return FakeResponse({"code": 422}, 422)
            if not isinstance(body.get("amount"), (int, float)):
                return FakeResponse({"code": 422}, 422)
            return FakeResponse({"id": 1}, 201)

    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)

    result = await api_runner.run(
        {
            "test_type": "api",
            "target_url": "https://api.example.test",
            "api_execution_policy": "write_allowed",
            "auth_headers": {"Authorization": "Bearer real-token"},
            "parsed_api_schema": [
                {
                    "method": "POST",
                    "path": "/users/export",
                    "summary": "Export users",
                    "auth_required": True,
                    "request_body_content_type": "application/json",
                    "request_body_schema": {
                        "type": "object",
                        "required": ["email", "password", "amount"],
                        "properties": {
                            "email": {"type": "string", "format": "email"},
                            "password": {"type": "string", "minLength": 8},
                            "amount": {"type": "number", "minimum": 1},
                        },
                    },
                    "response_status": "201",
                    "response_schema": {"type": "object", "properties": {"id": {"type": "integer"}}},
                }
            ],
            "workflow_steps": [],
        }
    )

    first_body = calls[0]["json"]
    tool_names = {call["tool"] for call in result["tool_calls"]}

    assert first_body["email"].count("@") == 1
    assert first_body["password"] == "TestClaw@123456"
    assert first_body["amount"] >= 1
    assert calls[0]["headers"]["Authorization"] == "Bearer real-token"
    assert result["api_execution_result"]["failed"] == 0
    assert result["api_execution_result"]["results"][0]["request_body_source"] == "faker_json_schema"
    assert "api.generate_mock_json_body" in tool_names


@pytest.mark.asyncio
async def test_api_runner_refreshes_expired_auth_and_retries_once(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        def __init__(self, payload: dict, status_code: int = 200) -> None:
            self._payload = payload
            self.status_code = status_code
            self.text = ""

        def json(self) -> dict:
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            calls.append({"method": method, "url": url, **kwargs})
            if (kwargs.get("headers") or {}).get("Authorization") == "Bearer fresh-token":
                return FakeResponse({"code": 200, "ok": True})
            return FakeResponse({"code": 401, "msg": "expired"})

    async def fake_resolve_auto_auth_headers(*args, **kwargs) -> AuthResolution:
        return AuthResolution(
            ok=True,
            headers={"Authorization": "Bearer fresh-token"},
            strategy="auto_login",
            header_name="Authorization",
            detail="ok",
        )

    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(api_runner, "resolve_auto_auth_headers", fake_resolve_auto_auth_headers)

    result = await api_runner.run(
        {
            "test_type": "api",
            "target_url": "https://api.example.test",
            "source_input": "{}",
            "input_type": "swagger_json",
            "auth_headers": {"Authorization": "Bearer expired-token"},
            "auth_config": {"enabled": True, "username": "admin", "password": "secret"},
            "parsed_api_schema": [
                {
                    "method": "GET",
                    "path": "/private",
                    "auth_required": True,
                    "response_status": "200",
                }
            ],
            "workflow_steps": [],
        }
    )

    api_result = result["api_execution_result"]
    assert len(calls) >= 2
    assert calls[0]["headers"]["Authorization"] == "Bearer expired-token"
    assert calls[1]["headers"]["Authorization"] == "Bearer fresh-token"
    assert api_result["failed"] == 0
    assert api_result["results"][0]["auth_refreshed"] is True
    assert "api.auth_refresh" in {call["tool"] for call in result["tool_calls"]}
    assert "secret" not in str(result["tool_calls"])


@pytest.mark.asyncio
async def test_api_runner_accepts_validation_business_error_envelope_500(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        status_code = 200
        text = ""

        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def json(self) -> dict:
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            calls.append({"method": method, "url": url, **kwargs})
            body = kwargs.get("json") or {}
            if isinstance(body.get("name"), str):
                return FakeResponse({"code": 200, "data": {"id": 1}})
            return FakeResponse({"code": 500, "msg": "JSON parse error"})

    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)

    result = await api_runner.run(
        {
            "test_type": "api",
            "target_url": "https://api.example.test",
            "api_execution_policy": "write_allowed",
            "parsed_api_schema": [
                {
                    "method": "POST",
                    "path": "/items/export",
                    "summary": "Export items",
                    "request_body_content_type": "application/json",
                    "request_body_schema": {
                        "type": "object",
                        "required": ["name"],
                        "properties": {"name": {"type": "string"}},
                    },
                    "response_status": "200",
                }
            ],
            "workflow_steps": [],
        }
    )

    validation_results = [
        item for item in result["api_execution_result"]["results"]
        if item.get("category") == "PARAM_VALIDATION"
    ]

    assert calls
    assert validation_results
    assert all(item.get("passed") is True for item in validation_results)
    assert all(item.get("accepted_error_envelope") is True for item in validation_results)
    assert all(item.get("envelope_status_code") == 500 for item in validation_results)
    assert all(item.get("warning_type") == "validation_business_error_envelope" for item in validation_results)


@pytest.mark.asyncio
async def test_tc_generator_uses_schema_safe_cases_for_all_get_objective(monkeypatch) -> None:
    monkeypatch.setattr(tc_generator.settings, "API_MAX_EXECUTED_REQUESTS", 4)

    result = await tc_generator.run(
        {
            "test_type": "api",
            "input_type": "swagger_json",
            "objective": "测试所有GET请求是否正常响应",
            "target_url": "https://api.example.test",
            "parsed_api_schema": [
                {"method": "GET", "path": f"/items/{index}", "response_status": "200"}
                for index in range(6)
            ]
            + [{"method": "POST", "path": "/items", "response_status": "200"}],
            "workflow_steps": [],
        }
    )

    assert len(result["api_cases"]) == 4
    assert result["api_cases_generated"] is True
    assert result["api_case_generation_source"] == "all_safe_schema"
    assert result["api_coverage_goal"] == "schema_driven_all_safe_get"
    assert [case["request_template"]["path"] for case in result["api_cases"]] == [
        "/items/0",
        "/items/1",
        "/items/2",
        "/items/3",
    ]


@pytest.mark.asyncio
async def test_model_strategy_all_documented_safe_methods_drives_schema_coverage(monkeypatch) -> None:
    calls = []

    class FakePlannerMessage:
        def __init__(self, payload: dict) -> None:
            self.content = json.dumps(payload)

    class FakePlanner:
        def __init__(self) -> None:
            self.calls = 0

        async def ainvoke(self, _messages):
            self.calls += 1
            if self.calls == 1:
                return FakePlannerMessage(
                    {
                        "api_plan": {"title": "API contract"},
                        "ui_plan": None,
                    }
                )
            return FakePlannerMessage(
                {
                    "intent": "api_read_only_coverage",
                    "coverage_scope": "all_documented_safe_methods",
                    "method_policy": {
                        "allowed_methods": ["GET", "HEAD", "OPTIONS"],
                        "blocked_methods": ["POST", "PUT", "PATCH", "DELETE"],
                        "write_allowed": False,
                    },
                    "endpoint_selection": {
                        "source": "schema",
                        "include": [],
                        "exclude": [],
                        "budget_behavior": "cover_all_within_budget",
                    },
                    "tool_plan": [
                        {
                            "tool_name": "api.derive_schema_requests",
                            "inputs": {"scope": "all_documented_safe_methods"},
                            "safety_constraints": ["schema_only", "safe_methods_only"],
                            "expected_observation": "safe request count",
                        }
                    ],
                    "case_generation_guidance": "Use documented response contracts only.",
                    "success_criteria": ["Every selected safe endpoint has evidence."],
                    "confidence": "high",
                    "reason": "The schema contains several read-only contract surfaces.",
                    "diagnostics": [],
                }
            )

    class FakeResponse:
        status_code = 200
        text = '{"ok": true}'
        headers = {"content-type": "application/json"}
        content = text.encode()

        def json(self) -> dict:
            return {"ok": True}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            calls.append({"method": method, "url": url, **kwargs})
            return FakeResponse()

    fake_planner = FakePlanner()

    async def fake_get_planner(_db):
        return fake_planner

    monkeypatch.setattr(planner.llm_gateway, "get_planner", fake_get_planner)
    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(api_runner.settings, "API_MAX_EXECUTED_REQUESTS", 10)

    planned = await planner.run(
        {
            "db_session": object(),
            "test_type": "api",
            "input_type": "swagger_json",
            "objective": "请按接口文档验证库存查询和字典读取能力",
            "target_url": "https://api.example.test",
            "api_execution_policy": "safe_read_only",
            "parsed_api_schema": [
                {"method": "GET", "path": "/stock", "response_status": "200"},
                {"method": "GET", "path": "/dict", "response_status": "200"},
                {"method": "POST", "path": "/stock", "response_status": "200"},
            ],
            "workflow_steps": [],
        }
    )
    result = await api_runner.run(planned)

    assert [call["url"] for call in calls] == [
        "https://api.example.test/stock",
        "https://api.example.test/dict",
    ]
    selection = result["api_execution_result"]["request_selection"]
    assert selection["source"] == "all_safe_schema"
    assert selection["coverage_scope"] == "all_documented_safe_methods"
    assert selection["strategy_source"] == "llm"
    assert selection["safe_endpoint_total"] == 2
    observations = result["agent_action_observations"]
    assert any(
        observation["tool_name"] == "api.derive_schema_requests"
        and observation["stage"] == "api_runner"
        and observation["status"] == "success"
        and observation["output"]["selected_total"] == 2
        for observation in observations
    )
    assert any(
        trace["tool"] == "api.derive_schema_requests"
        and trace["observation"].startswith("success; selected_total=2")
        for trace in result["agent_react_trace"]
    )


@pytest.mark.asyncio
async def test_planner_llm_timeout_records_progress_and_uses_fallback_strategy(monkeypatch) -> None:
    class SlowPlanner:
        calls = 0

        async def ainvoke(self, _messages):
            self.calls += 1
            await asyncio.sleep(1)

    slow_planner = SlowPlanner()

    async def fake_get_planner(_db):
        return slow_planner

    monkeypatch.setattr(settings, "AGENT_PLAN_LLM_TIMEOUT_SECONDS", 0.001)
    monkeypatch.setattr(planner.llm_gateway, "get_planner", fake_get_planner)

    state = await planner.run(
        {
            "db_session": object(),
            "test_type": "api",
            "input_type": "swagger_json",
            "objective": "请覆盖所有已文档化的安全接口，记录每个接口的执行证据",
            "target_url": "https://api.example.test",
            "api_execution_policy": "safe_read_only",
            "parsed_api_schema": [
                {"method": "GET", "path": "/stock", "response_status": "200"},
                {"method": "GET", "path": "/dict", "response_status": "200"},
                {"method": "POST", "path": "/stock", "response_status": "200"},
            ],
            "workflow_steps": [],
        }
    )

    assert slow_planner.calls == 2
    assert [step["status"] for step in state["workflow_steps"] if step["node"] == "planner"] == [
        "done"
    ]
    progress = [
        event["status"] for event in state["progress_events"] if event["node"] == "planner"
    ]
    assert progress == ["running", "done"]
    strategy = state["agent_strategy_decision"]
    assert strategy["source"] == "agent_strategy_fallback"
    assert strategy["coverage_scope"] == "all_documented_safe_methods"
    assert "planner.strategy timed out" in strategy["fallback_reason"]


@pytest.mark.asyncio
async def test_planner_attaches_only_high_confidence_target_memory_to_plan() -> None:
    planned = await planner.run(
        {
            "test_type": "api",
            "input_type": "swagger_json",
            "objective": "Repeat profile auth regression",
            "target_url": "https://api.example.test",
            "source_input": "https://api.example.test/openapi.json",
            "api_execution_policy": "safe_read_only",
            "parsed_api_schema": [
                {"method": "GET", "path": "/profile", "response_status": "200"},
            ],
            "rag_retrieval": {"sources": [{"id": "knowledge-1"}]},
            "rag_facts": [
                {
                    "fact_type": "known_blocker",
                    "confidence": "high",
                    "source_id": "knowledge-1",
                    "source_script_id": "run-previous",
                    "target_hint": "https://api.example.test/profile",
                    "stage": "api",
                    "failure_type": "auth_failure",
                    "next_action": "ask_human",
                    "summary": "Profile reads were blocked by missing auth.",
                    "planner_hint": "Ask for valid auth headers before replaying /profile.",
                },
                {
                    "fact_type": "known_blocker",
                    "confidence": "low",
                    "source_id": "knowledge-low",
                    "target_hint": "https://api.example.test/profile",
                    "failure_type": "schema_contract",
                    "planner_hint": "Low confidence fact should not be used.",
                },
                {
                    "fact_type": "known_blocker",
                    "confidence": "high",
                    "source_id": "knowledge-other",
                    "target_hint": "https://other.example.test/profile",
                    "failure_type": "network_error",
                    "planner_hint": "Different target should not be used.",
                },
            ],
            "workflow_steps": [],
        }
    )

    api_plan = planned["api_plan"]
    serialized = json.dumps(api_plan, ensure_ascii=False)
    assert api_plan["memory_fact_count"] == 1
    assert api_plan["known_blockers"][0]["failure_type"] == "auth_failure"
    assert api_plan["known_blockers"][0]["source_script_id"] == "run-previous"
    assert "schema_contract" not in serialized
    assert "network_error" not in serialized
    tool_call = next(
        item
        for item in planned["tool_calls"]
        if item["tool"] == "planner.generate_execution_plan"
    )
    assert tool_call["output"]["memory_fact_count"] == 1
    assert tool_call["output"]["memory_blocker_count"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("coverage_scope", "budget_behavior"),
    [
        ("focused_documented_endpoints", "focused_only"),
        ("sampled_contract", "sample_representative"),
    ],
)
async def test_model_strategy_focused_or_sampled_scope_does_not_force_all_schema(
    monkeypatch,
    coverage_scope: str,
    budget_behavior: str,
) -> None:
    calls = []

    class FakePlannerMessage:
        def __init__(self, payload: dict) -> None:
            self.content = json.dumps(payload)

    class FakePlanner:
        def __init__(self) -> None:
            self.calls = 0

        async def ainvoke(self, _messages):
            self.calls += 1
            if self.calls == 1:
                return FakePlannerMessage({"api_plan": {"title": "API focus"}, "ui_plan": None})
            return FakePlannerMessage(
                {
                    "intent": "api_focused_endpoints",
                    "coverage_scope": coverage_scope,
                    "method_policy": {"allowed_methods": ["GET"], "write_allowed": False},
                    "endpoint_selection": {
                        "source": "model_focus",
                        "include": [
                            {"method": "GET", "path": "/orders"},
                            {"method": "GET", "path": "/profile"},
                        ],
                        "exclude": [],
                        "budget_behavior": budget_behavior,
                    },
                    "tool_plan": [
                        {
                            "tool_name": "api.derive_schema_requests",
                            "inputs": {"scope": coverage_scope},
                            "safety_constraints": ["schema_only", "local_method_policy"],
                            "expected_observation": "focused request count",
                        }
                    ],
                    "case_generation_guidance": "Only execute selected documented endpoints.",
                    "success_criteria": ["Selected endpoints have evidence."],
                    "confidence": "high",
                    "reason": "The objective focuses on order and profile reads.",
                    "diagnostics": [],
                }
            )

    class FakeResponse:
        status_code = 200
        text = '{"ok": true}'
        headers = {"content-type": "application/json"}
        content = text.encode()

        def json(self) -> dict:
            return {"ok": True}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            calls.append({"method": method, "url": url, **kwargs})
            return FakeResponse()

    async def fake_get_planner(_db):
        return FakePlanner()

    monkeypatch.setattr(planner.llm_gateway, "get_planner", fake_get_planner)
    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(api_runner.settings, "API_MAX_EXECUTED_REQUESTS", 10)

    planned = await planner.run(
        {
            "db_session": object(),
            "test_type": "api",
            "input_type": "swagger_json",
            "objective": "验证订单和用户资料读取契约",
            "target_url": "https://api.example.test",
            "api_execution_policy": "safe_read_only",
            "parsed_api_schema": [
                {"method": "GET", "path": "/orders", "response_status": "200"},
                {"method": "GET", "path": "/profile", "response_status": "200"},
                {"method": "GET", "path": "/health", "response_status": "200"},
            ],
            "workflow_steps": [],
        }
    )
    result = await api_runner.run(planned)

    assert [call["url"] for call in calls] == [
        "https://api.example.test/orders",
        "https://api.example.test/profile",
    ]
    selection = result["api_execution_result"]["request_selection"]
    assert selection["source"] == "agent_strategy_schema"
    assert selection["coverage_scope"] == coverage_scope
    assert selection["strategy_endpoint_total"] == 2
    assert selection["candidate_total"] == 2


@pytest.mark.asyncio
async def test_model_strategy_guardrail_drops_write_and_out_of_schema_endpoints(monkeypatch) -> None:
    calls = []

    class FakePlannerMessage:
        def __init__(self, payload: dict) -> None:
            self.content = json.dumps(payload)

    class FakePlanner:
        def __init__(self) -> None:
            self.calls = 0

        async def ainvoke(self, _messages):
            self.calls += 1
            if self.calls == 1:
                return FakePlannerMessage({"api_plan": {"title": "API guarded"}, "ui_plan": None})
            return FakePlannerMessage(
                {
                    "intent": "api_focused_endpoints",
                    "coverage_scope": "focused_documented_endpoints",
                    "method_policy": {"allowed_methods": ["GET", "POST"], "write_allowed": True},
                    "endpoint_selection": {
                        "source": "model_focus",
                        "include": [
                            {"method": "POST", "path": "/items"},
                            {"method": "GET", "path": "/ghost"},
                            {"method": "GET", "path": "/health"},
                        ],
                        "exclude": [],
                        "budget_behavior": "focused_only",
                    },
                    "tool_plan": [
                        {
                            "tool_name": "api.derive_schema_requests",
                            "inputs": {"scope": "focused_documented_endpoints"},
                            "safety_constraints": ["schema_only"],
                            "expected_observation": "guarded request count",
                        },
                        {
                            "tool_name": "api.unknown_tool",
                            "inputs": {},
                            "safety_constraints": [],
                            "expected_observation": "should be dropped",
                        },
                    ],
                    "case_generation_guidance": "Use selected endpoints.",
                    "success_criteria": ["Only safe documented endpoints execute."],
                    "confidence": "medium",
                    "reason": "The model selected a mixed endpoint list.",
                    "diagnostics": [],
                }
            )

    class FakeResponse:
        status_code = 200
        text = '{"ok": true}'
        headers = {"content-type": "application/json"}
        content = text.encode()

        def json(self) -> dict:
            return {"ok": True}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            calls.append({"method": method, "url": url, **kwargs})
            return FakeResponse()

    async def fake_get_planner(_db):
        return FakePlanner()

    monkeypatch.setattr(planner.llm_gateway, "get_planner", fake_get_planner)
    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(api_runner.settings, "API_MAX_EXECUTED_REQUESTS", 10)

    planned = await planner.run(
        {
            "db_session": object(),
            "test_type": "api",
            "input_type": "swagger_json",
            "objective": "验证健康检查",
            "target_url": "https://api.example.test",
            "api_execution_policy": "safe_read_only",
            "parsed_api_schema": [
                {"method": "GET", "path": "/health", "response_status": "200"},
                {"method": "POST", "path": "/items", "response_status": "200"},
            ],
            "workflow_steps": [],
        }
    )
    result = await api_runner.run(planned)

    assert [call["url"] for call in calls] == ["https://api.example.test/health"]
    diagnostics = result["agent_strategy_decision"]["diagnostics"]
    assert any(item["kind"] == "method_blocked_by_policy" for item in diagnostics)
    assert any(item["kind"] == "out_of_schema_endpoint" for item in diagnostics)
    assert any(item["kind"] == "unknown_tool_name" for item in diagnostics)
    action_by_tool = {action["tool_name"]: action for action in result["agent_actions"]}
    assert action_by_tool["api.unknown_tool"]["allowed"] is False
    assert any(
        item["kind"] == "unknown_tool_name"
        for item in action_by_tool["api.unknown_tool"]["diagnostics"]
    )
    assert action_by_tool["api.derive_schema_requests"]["allowed"] is True
    assert all(call["method"] == "GET" for call in calls)


@pytest.mark.parametrize("execution_policy", ["safe_read_only", "safe_with_auth"])
def test_strategy_normalizer_blocks_write_and_out_of_schema_under_safe_policies(
    execution_policy: str,
) -> None:
    decision = normalize_agent_strategy_decision(
        {
            "intent": "api_focused_endpoints",
            "coverage_scope": "focused_documented_endpoints",
            "method_policy": {
                "allowed_methods": ["GET", "POST", "DELETE"],
                "write_allowed": True,
            },
            "endpoint_selection": {
                "source": "model_focus",
                "include": [
                    {"method": "POST", "path": "/items"},
                    {"method": "DELETE", "path": "/items/1"},
                    {"method": "GET", "path": "/ghost"},
                    {"method": "GET", "path": "/health"},
                ],
                "budget_behavior": "focused_only",
            },
            "tool_plan": [
                {"tool_name": "api.http_request", "inputs": {}},
                {"tool_name": "api.missing_tool", "inputs": {}},
            ],
            "confidence": "high",
            "reason": "Validate safe policy enforcement.",
        },
        parsed_api_schema=[
            {"method": "GET", "path": "/health", "response_status": "200"},
            {"method": "POST", "path": "/items", "response_status": "200"},
            {"method": "DELETE", "path": "/items/{id}", "response_status": "204"},
        ],
        execution_policy=execution_policy,
        test_type="api",
        source="llm",
    )

    assert decision["method_policy"]["write_allowed"] is False
    assert decision["method_policy"]["allowed_methods"] == ["GET"]
    assert decision["endpoint_selection"]["include"] == [{"method": "GET", "path": "/health"}]
    assert [step["tool_name"] for step in decision["tool_plan"]] == [
        "api.http_request",
        "api.missing_tool",
        "api.derive_schema_requests",
    ]
    diagnostics = decision["diagnostics"]
    assert any(item["kind"] == "write_policy_overridden" for item in diagnostics)
    assert any(item["kind"] == "method_blocked_by_policy" and item["method"] == "POST" for item in diagnostics)
    assert any(item["kind"] == "method_blocked_by_policy" and item["method"] == "DELETE" for item in diagnostics)
    assert any(item["kind"] == "out_of_schema_endpoint" and item["path"] == "/ghost" for item in diagnostics)
    assert any(item["kind"] == "unknown_tool_name" for item in diagnostics)


def test_strategy_normalizer_uses_schema_tool_scope_when_focused_scope_has_no_include() -> None:
    decision = normalize_agent_strategy_decision(
        {
            "intent": "api_focused_endpoints",
            "coverage_scope": "focused_documented_endpoints",
            "method_policy": {"allowed_methods": ["GET"], "write_allowed": False},
            "endpoint_selection": {
                "source": "schema",
                "include": [],
                "budget_behavior": "cover_all_within_budget",
            },
            "tool_plan": [
                {
                    "tool_name": "api.derive_schema_requests",
                    "inputs": {"scope": "all_documented_safe_methods"},
                }
            ],
            "reason": "Derive documented safe methods.",
        },
        parsed_api_schema=[
            {"method": "GET", "path": "/warehouse/list", "response_status": "200"},
            {"method": "POST", "path": "/warehouse", "response_status": "200"},
        ],
        execution_policy="safe_read_only",
        test_type="api",
        source="llm",
    )

    assert decision["intent"] == "api_read_only_coverage"
    assert decision["coverage_scope"] == "all_documented_safe_methods"
    assert decision["endpoint_selection"]["include"] == [
        {"method": "GET", "path": "/warehouse/list"}
    ]
    assert any(item["kind"] == "coverage_scope_tool_mismatch" for item in decision["diagnostics"])


def test_strategy_normalizer_requires_live_observation_before_ui_blocked_report() -> None:
    decision = normalize_agent_strategy_decision(
        {
            "intent": "blocked",
            "coverage_scope": "none",
            "tool_plan": [{"tool_name": "memory.retrieve_rag_context", "inputs": {}}],
            "reason": "Historical memory suggests the page once failed.",
        },
        parsed_api_schema=None,
        execution_policy="safe_read_only",
        test_type="ui",
        source="llm",
    )

    assert decision["intent"] == "ui_exploration"
    assert decision["coverage_scope"] == "ui_paths"
    assert any(item["kind"] == "ui_live_observation_required" for item in decision["diagnostics"])


def test_planner_schema_summary_includes_safe_endpoint_examples() -> None:
    summary = json.loads(
        planner._api_schema_prompt_summary(
            [
                {"method": "POST", "path": "/warehouse", "summary": "新增仓库"},
                {"method": "GET", "path": "/warehouse/list", "summary": "查询仓库列表"},
            ],
            [{"method": "POST", "path": "/warehouse", "summary": "新增仓库"}],
        )
    )

    assert summary["endpoint_count"] == 2
    assert summary["safe_method_endpoint_count"] == 1
    assert summary["safe_endpoint_examples"] == [
        {
            "path": "/warehouse/list",
            "method": "GET",
            "summary": "查询仓库列表",
            "auth_required": None,
            "required_fields": [],
        }
    ]


def test_execution_evaluator_suppresses_model_replan_after_reportable_schema_evidence() -> None:
    state = {
        "test_type": "api",
        "parsed_api_schema": [{"method": "GET", "path": f"/items/{index}"} for index in range(12)],
        "api_execution_result": {
            "total": 12,
            "executed": 12,
            "http_executed": 12,
            "passed": 10,
            "failed": 2,
            "complete": True,
            "request_selection": {
                "source": "parsed_api_schema",
                "selected_total": 12,
            },
        },
    }
    guardrail = {
        "sufficient_evidence": True,
        "confidence": "medium",
        "next_action": "report",
        "reason": "API evidence is sufficient for this bounded pass.",
        "diagnostics": [],
        "missing_evidence": [],
        "replan_instructions": "",
        "source": "guardrail",
    }
    model_decision = {
        "sufficient_evidence": False,
        "confidence": "medium",
        "next_action": "replan_api",
        "reason": "Try more domain endpoints.",
        "diagnostics": ["needs more domain endpoints"],
        "missing_evidence": ["domain endpoints"],
        "replan_instructions": "Regenerate domain cases.",
        "source": "llm",
    }

    decision = execution_evaluator._merge_decisions(
        guardrail=guardrail,
        model_decision=model_decision,
        state=state,
        stage="api",
    )

    assert decision["next_action"] == "report"
    assert "suppressed" in decision["reason"]
    assert any("已停止继续重规划" in item for item in decision["diagnostics"])


@pytest.mark.asyncio
async def test_api_runner_uses_schema_safe_budget_for_all_get_objective(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        status_code = 200
        text = '{"ok": true}'
        headers = {"content-type": "application/json"}
        content = text.encode()

        def json(self) -> dict:
            return {"ok": True}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            calls.append({"method": method, "url": url, **kwargs})
            return FakeResponse()

    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(api_runner.settings, "API_MAX_EXECUTED_REQUESTS", 3)

    result = await api_runner.run(
        {
            "test_type": "api",
            "target_url": "https://api.example.test",
            "objective": "基于接口文档测试所有的GET请求是否正常响应",
            "api_execution_policy": "safe_read_only",
            "api_cases": [
                {"title": "small generated sample", "request_template": {"method": "GET", "path": "/sample"}}
            ],
            "parsed_api_schema": [
                {"method": "GET", "path": f"/schema/{index}", "response_status": "200"}
                for index in range(5)
            ]
            + [{"method": "POST", "path": "/schema/write", "response_status": "200"}],
            "workflow_steps": [],
        }
    )

    api_result = result["api_execution_result"]

    assert [call["url"] for call in calls] == [
        "https://api.example.test/schema/0",
        "https://api.example.test/schema/1",
        "https://api.example.test/schema/2",
    ]
    assert api_result["total"] == 3
    assert api_result["candidate_total"] == 5
    assert api_result["request_selection"]["source"] == "all_safe_schema"
    assert api_result["request_selection"]["coverage_goal"] == "schema_driven_all_safe_get"
    assert api_result["request_selection"]["safe_endpoint_total"] == 5
    assert api_result["request_selection"]["selected_safe_endpoint_total"] == 3
    assert api_result["request_selection"]["omitted_safe_endpoint_total"] == 2
    assert api_result["request_selection"]["bounded"] is True
    assert api_result["budget_skipped"] == 2


@pytest.mark.asyncio
async def test_execution_evaluator_does_not_replan_schema_driven_all_get_coverage(monkeypatch) -> None:
    class FakePlannerMessage:
        content = json.dumps(
            {
                "sufficient_evidence": False,
                "confidence": "high",
                "next_action": "replan_api",
                "reason": "Generate more API cases.",
                "diagnostics": ["api_cases count is small"],
                "missing_evidence": ["more api_cases"],
                "replan_instructions": "Generate all GET requests again.",
            }
        )

    class FakePlanner:
        async def ainvoke(self, _messages):
            return FakePlannerMessage()

    async def fake_get_planner(_db):
        return FakePlanner()

    monkeypatch.setattr(execution_evaluator.llm_gateway, "get_planner", fake_get_planner)
    monkeypatch.setattr(execution_evaluator.settings, "AGENT_MAX_REPLAN_ATTEMPTS", 2)

    result = await execution_evaluator.run(
        {
            "db_session": object(),
            "test_type": "api",
            "input_type": "swagger_json",
            "objective": "测试所有GET请求",
            "agent_execution_stage": "api",
            "parsed_api_schema": [
                {"method": "GET", "path": f"/items/{index}", "response_status": "200"}
                for index in range(5)
            ],
            "api_cases": [{"title": "visible case"}],
            "api_execution_result": {
                "total": 3,
                "candidate_total": 5,
                "executed": 3,
                "passed": 3,
                "failed": 0,
                "skipped": 0,
                "http_executed": 3,
                "all_passed": True,
                "complete": True,
                "request_selection": {
                    "source": "all_safe_schema",
                    "coverage_goal": "schema_driven_all_safe_get",
                    "candidate_total": 5,
                    "selected_total": 3,
                    "safe_endpoint_total": 5,
                    "selected_safe_endpoint_total": 3,
                    "omitted_safe_endpoint_total": 2,
                    "omitted": 2,
                    "bounded": True,
                },
                "results": [],
            },
            "workflow_steps": [],
        }
    )

    assert result["evidence_evaluation"]["next_action"] == "report"
    assert result["agent_next_node"] == "reporter"
    assert result["api_cases"] == [{"title": "visible case"}]
    assert result.get("agent_replan_counts", {}).get("api") is None


@pytest.mark.asyncio
async def test_execution_evaluator_does_not_replan_completed_model_strategy_scope(monkeypatch) -> None:
    class FakePlannerMessage:
        content = json.dumps(
            {
                "sufficient_evidence": False,
                "confidence": "high",
                "next_action": "replan_api",
                "reason": "Only two API cases were generated.",
                "diagnostics": ["api_cases count is small"],
                "missing_evidence": ["more api_cases"],
                "replan_instructions": "Generate broader API cases.",
            }
        )

    class FakePlanner:
        async def ainvoke(self, _messages):
            return FakePlannerMessage()

    async def fake_get_planner(_db):
        return FakePlanner()

    monkeypatch.setattr(execution_evaluator.llm_gateway, "get_planner", fake_get_planner)
    monkeypatch.setattr(execution_evaluator.settings, "AGENT_MAX_REPLAN_ATTEMPTS", 2)

    result = await execution_evaluator.run(
        {
            "db_session": object(),
            "test_type": "api",
            "input_type": "swagger_json",
            "objective": "验证订单和用户资料读取契约",
            "agent_execution_stage": "api",
            "agent_strategy_decision": {
                "intent": "api_focused_endpoints",
                "coverage_scope": "focused_documented_endpoints",
                "source": "llm",
                "valid": True,
                "endpoint_selection": {
                    "source": "model_focus",
                    "include": [
                        {"method": "GET", "path": "/orders"},
                        {"method": "GET", "path": "/profile"},
                    ],
                    "budget_behavior": "focused_only",
                },
            },
            "parsed_api_schema": [
                {"method": "GET", "path": "/orders", "response_status": "200"},
                {"method": "GET", "path": "/profile", "response_status": "200"},
                {"method": "GET", "path": "/health", "response_status": "200"},
            ],
            "api_cases": [{"title": "orders"}, {"title": "profile"}],
            "api_execution_result": {
                "total": 2,
                "candidate_total": 2,
                "executed": 2,
                "passed": 2,
                "failed": 0,
                "skipped": 0,
                "http_executed": 2,
                "all_passed": True,
                "complete": True,
                "request_selection": {
                    "source": "agent_strategy_schema",
                    "coverage_scope": "focused_documented_endpoints",
                    "coverage_goal": "focused_documented_endpoints",
                    "strategy_intent": "api_focused_endpoints",
                    "strategy_endpoint_total": 2,
                    "selected_strategy_endpoint_total": 2,
                    "omitted_strategy_endpoint_total": 0,
                    "strategy_coverage_completed": True,
                    "candidate_total": 2,
                    "selected_total": 2,
                    "bounded": False,
                },
                "results": [],
            },
            "workflow_steps": [],
        }
    )

    assert result["evidence_evaluation"]["next_action"] == "report"
    assert result["agent_next_node"] == "reporter"
    assert result["api_cases"] == [{"title": "orders"}, {"title": "profile"}]
    assert result.get("agent_replan_counts", {}).get("api") is None


@pytest.mark.asyncio
async def test_api_runner_prefers_curated_api_cases_over_schema(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        status_code = 200
        text = '{"ok": true}'
        headers = {"content-type": "application/json"}
        content = text.encode()

        def json(self) -> dict:
            return {"ok": True}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            calls.append({"method": method, "url": url, **kwargs})
            return FakeResponse()

    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(api_runner.settings, "API_MAX_EXECUTED_REQUESTS", 120)

    result = await api_runner.run(
        {
            "test_type": "api",
            "target_url": "https://api.example.test",
            "api_cases": [
                {"title": "curated health", "request_template": {"method": "GET", "path": "/health"}}
            ],
            "parsed_api_schema": [
                {"method": "GET", "path": f"/schema/{index}", "response_status": "200"}
                for index in range(5)
            ],
            "workflow_steps": [],
        }
    )

    api_result = result["api_execution_result"]

    assert [call["url"] for call in calls] == ["https://api.example.test/health"]
    assert api_result["total"] == 1
    assert api_result["candidate_total"] == 1
    assert api_result["request_selection"]["source"] == "api_cases"
    assert api_result["results"][0]["label"] == "curated health"


@pytest.mark.asyncio
async def test_api_runner_falls_back_to_safe_schema_when_cases_are_not_safe_executable(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        status_code = 200
        text = '{"ok": true}'
        headers = {"content-type": "application/json"}
        content = text.encode()

        def json(self) -> dict:
            return {"ok": True}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            calls.append({"method": method, "url": url, **kwargs})
            return FakeResponse()

    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(api_runner.settings, "API_MAX_EXECUTED_REQUESTS", 120)

    result = await api_runner.run(
        {
            "test_type": "api",
            "target_url": "https://api.example.test",
            "api_execution_policy": "safe_read_only",
            "api_cases": [
                {"title": "curated create", "request_template": {"method": "POST", "path": "/items"}}
            ],
            "parsed_api_schema": [
                {"method": "GET", "path": "/health", "response_status": "200"},
                {"method": "POST", "path": "/items", "response_status": "200"},
            ],
            "workflow_steps": [],
        }
    )

    api_result = result["api_execution_result"]

    assert [call["url"] for call in calls] == ["https://api.example.test/health"]
    assert api_result["total"] == 1
    assert api_result["executed"] == 1
    assert api_result["skipped"] == 0
    assert api_result["request_selection"]["source"] == "safe_schema_fallback"
    assert api_result["request_selection"]["fallback_reason"] == "curated_api_cases_not_executable_under_policy"


@pytest.mark.asyncio
async def test_api_runner_write_allowed_blocks_mutating_schema_requests(monkeypatch) -> None:
    calls = []

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs):
            calls.append({"method": method, "url": url, **kwargs})
            raise AssertionError("mutating write request should be blocked before HTTP execution")

    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(api_runner.settings, "API_MAX_EXECUTED_REQUESTS", 120)

    result = await api_runner.run(
        {
            "test_type": "api",
            "target_url": "http://api.example.test",
            "api_execution_policy": "write_allowed",
            "parsed_api_schema": [
                {
                    "method": "POST",
                    "path": "/items",
                    "summary": "Create item",
                    "request_body_content_type": "application/json",
                    "request_body_schema": {
                        "type": "object",
                        "required": ["name"],
                        "properties": {"name": {"type": "string"}},
                    },
                    "response_status": "200",
                },
                {
                    "method": "DELETE",
                    "path": "/items/{id}",
                    "summary": "Delete item",
                    "response_status": "200",
                },
            ],
            "workflow_steps": [],
        }
    )

    api_result = result["api_execution_result"]

    assert calls == []
    assert api_result["http_executed"] == 0
    assert api_result["executed"] == 0
    assert api_result["skipped"] == api_result["total"]
    assert {item.get("skip_type") for item in api_result["results"]} == {
        api_runner.SAFE_WRITE_BLOCK_SKIP_TYPE
    }
    assert {observation["failure_type"] for observation in result["agent_observations"]} == {
        "safe_write_blocked"
    }
    assert {
        observation["outputs"]["safety_decision"]
        for observation in result["agent_observations"]
    } == {"blocked_by_safe_write_gate"}
    assert "api.safe_write_gate" in {call["tool"] for call in result["tool_calls"]}


@pytest.mark.asyncio
async def test_api_runner_skips_same_method_writes_after_environment_405(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        status_code = 405
        text = "<html><body>405 Not Allowed<hr><center>nginx</center></body></html>"
        headers = {"content-type": "text/html"}
        content = text.encode()

        def json(self) -> dict:
            raise ValueError("not json")

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            calls.append({"method": method, "url": url, **kwargs})
            return FakeResponse()

    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(api_runner.settings, "API_MAX_EXECUTED_REQUESTS", 120)

    result = await api_runner.run(
        {
            "test_type": "api",
            "target_url": "http://api.example.test",
            "api_execution_policy": "write_allowed",
            "parsed_api_schema": [
                {
                    "method": "POST",
                    "path": "/items/export",
                    "summary": "Export items",
                    "request_body_content_type": "application/json",
                    "request_body_schema": {
                        "type": "object",
                        "required": ["name"],
                        "properties": {"name": {"type": "string"}},
                    },
                    "response_status": "200",
                },
                {
                    "method": "POST",
                    "path": "/areas/export",
                    "summary": "Export areas",
                    "request_body_content_type": "application/json",
                    "request_body_schema": {
                        "type": "object",
                        "required": ["areaName"],
                        "properties": {"areaName": {"type": "string"}},
                    },
                    "response_status": "200",
                },
            ],
            "workflow_steps": [],
        }
    )

    api_result = result["api_execution_result"]

    assert len(calls) == 1
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == "http://api.example.test/items/export"
    assert api_result["total"] > 1
    assert api_result["http_executed"] == 1
    assert api_result["executed"] == 0
    assert api_result["failed"] == 0
    assert api_result["skipped"] == api_result["total"]
    assert api_result["environment_skipped"] == api_result["total"]
    assert api_result["results"][0]["status_code"] == 405
    assert api_result["results"][0]["skip_type"] == "environment_not_executable"
    assert all(item.get("skipped") for item in api_result["results"])


@pytest.mark.asyncio
async def test_api_runner_respects_http_execution_budget_and_omits_remainder(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        status_code = 200
        text = '{"ok": true}'
        headers = {"content-type": "application/json"}
        content = text.encode()

        def json(self) -> dict:
            return {"ok": True}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            calls.append({"method": method, "url": url, **kwargs})
            return FakeResponse()

    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(api_runner.settings, "API_MAX_EXECUTED_REQUESTS", 2)

    result = await api_runner.run(
        {
            "test_type": "api",
            "target_url": "http://api.example.test",
            "parsed_api_schema": [
                {"method": "GET", "path": f"/items/{index}", "response_status": "200"}
                for index in range(5)
            ],
            "workflow_steps": [],
        }
    )

    api_result = result["api_execution_result"]

    assert len(calls) == 2
    assert api_result["total"] == 2
    assert api_result["candidate_total"] == 5
    assert len(api_result["results"]) == 2
    assert api_result["http_executed"] == 2
    assert api_result["executed"] == 2
    assert api_result["passed"] == 2
    assert api_result["failed"] == 0
    assert api_result["skipped"] == 0
    assert api_result["budget_skipped"] == 3
    assert api_result["omitted"] == 3
    assert api_result["budget_exhausted"] is True
    assert api_result["complete"] is True
    assert api_result["request_selection"]["budget_omitted"] == 3
    assert not any(
        item.get("skip_type") == "execution_budget_exhausted"
        for item in api_result["results"]
    )


@pytest.mark.asyncio
async def test_api_runner_execution_budget_caps_retries(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        status_code = 500
        text = '{"error": "temporary"}'
        headers = {"content-type": "application/json"}
        content = text.encode()

        def json(self) -> dict:
            return {"error": "temporary"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            calls.append({"method": method, "url": url, **kwargs})
            return FakeResponse()

    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(api_runner.settings, "API_MAX_EXECUTED_REQUESTS", 2)
    monkeypatch.setattr(api_runner.settings, "API_REQUEST_RETRY_COUNT", 5)

    result = await api_runner.run(
        {
            "test_type": "api",
            "target_url": "http://api.example.test",
            "parsed_api_schema": [
                {"method": "GET", "path": f"/items/{index}", "response_status": "200"}
                for index in range(3)
            ],
            "workflow_steps": [],
        }
    )

    api_result = result["api_execution_result"]

    assert len(calls) == 2
    assert {call["url"] for call in calls} == {"http://api.example.test/items/0"}
    assert api_result["http_executed"] == 2
    assert api_result["executed"] == 1
    assert api_result["failed"] == 1
    assert len(api_result["results"]) == 1
    assert api_result["budget_skipped"] == 2
    assert api_result["omitted"] == 2
    assert api_result["budget_exhausted"] is True
    assert api_result["request_selection"]["budget_omitted"] == 1
    assert api_result["request_selection"]["runtime_budget_omitted"] == 1
    assert not any(
        item.get("skip_type") == "execution_budget_exhausted"
        for item in api_result["results"]
    )


@pytest.mark.asyncio
async def test_api_runner_auth_negative_200_is_advisory_not_main_failure(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        status_code = 200
        text = '{"ok": true}'
        headers = {"content-type": "application/json"}
        content = text.encode()

        def json(self) -> dict:
            return {"ok": True}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            calls.append({"method": method, "url": url, **kwargs})
            return FakeResponse()

    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(api_runner.settings, "API_MAX_EXECUTED_REQUESTS", 120)

    result = await api_runner.run(
        {
            "test_type": "api",
            "target_url": "http://api.example.test",
            "auth_headers": {"Authorization": "Bearer real-token"},
            "parsed_api_schema": [
                {
                    "method": "GET",
                    "path": "/private",
                    "auth_required": True,
                    "response_status": "200",
                }
            ],
            "workflow_steps": [],
        }
    )

    api_result = result["api_execution_result"]
    auth_result = next(item for item in api_result["results"] if item["category"] == "AUTH")
    report_state = await reporter.run(result)

    assert len(calls) == 2
    assert api_result["passed"] == 1
    assert api_result["failed"] == 0
    assert api_result["skipped"] == 1
    assert api_result["advisory"] == 1
    assert auth_result["advisory_type"] == "auth_negative_unexpected_success"
    assert auth_result["passed"] is None
    assert report_state["final_report"]["overall_verdict"] == "PASS"
    assert report_state["final_report"]["advisory_findings"]


@pytest.mark.asyncio
async def test_api_runner_auth_negative_401_still_passes(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        text = "{}"
        headers = {"content-type": "application/json"}
        content = text.encode()

        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

        def json(self) -> dict:
            return {}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            calls.append({"method": method, "url": url, **kwargs})
            if (kwargs.get("headers") or {}).get("Authorization"):
                return FakeResponse(200)
            return FakeResponse(401)

    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(api_runner.settings, "API_MAX_EXECUTED_REQUESTS", 120)

    result = await api_runner.run(
        {
            "test_type": "api",
            "target_url": "http://api.example.test",
            "auth_headers": {"Authorization": "Bearer real-token"},
            "parsed_api_schema": [
                {
                    "method": "GET",
                    "path": "/private",
                    "auth_required": True,
                    "response_status": "200",
                }
            ],
            "workflow_steps": [],
        }
    )

    api_result = result["api_execution_result"]

    assert len(calls) == 2
    assert api_result["passed"] == 2
    assert api_result["failed"] == 0
    assert api_result["skipped"] == 0
    assert api_result["advisory"] == 0


@pytest.mark.asyncio
async def test_api_runner_auth_case_strips_template_auth_headers_and_matches_envelope_401(
    monkeypatch,
) -> None:
    calls = []

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        def __init__(self, payload: dict) -> None:
            self._payload = payload
            self.text = json.dumps(payload)
            self.content = self.text.encode()

        def json(self) -> dict:
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            calls.append({"method": method, "url": url, **kwargs})
            headers = kwargs.get("headers") or {}
            if url.endswith("/private-no-token"):
                assert not any(api_runner.is_sensitive_header(name) for name in headers)
                return FakeResponse({"code": 401, "msg": "认证失败，无法访问系统资源"})
            assert headers.get("Authorization") == "Bearer real-token"
            return FakeResponse({"ok": True})

    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(api_runner.settings, "API_MAX_EXECUTED_REQUESTS", 120)

    result = await api_runner.run(
        {
            "test_type": "api",
            "target_url": "https://api.example.test",
            "auth_headers": {"Authorization": "Bearer real-token", "Cookie": "sid=secret"},
            "api_cases": [
                {
                    "title": "无Token访问",
                    "category": "AUTH",
                    "expected_status": [401, 403],
                    "request_template": {
                        "method": "GET",
                        "path": "/private-no-token",
                        "headers": {
                            "Authorization": REDACTED_VALUE,
                            "Cookie": "sid=template",
                            "X-API-Key": REDACTED_VALUE,
                            "API-Key": REDACTED_VALUE,
                            "X-Token": REDACTED_VALUE,
                            "X-Trace": "keep-me",
                        },
                    },
                },
                {
                    "title": "with token",
                    "category": "SMOKE",
                    "request_template": {
                        "method": "GET",
                        "path": "/private",
                        "headers": {
                            "Authorization": REDACTED_VALUE,
                            "authorization": REDACTED_VALUE,
                            "Cookie": "sid=template",
                            "X-Token": REDACTED_VALUE,
                            "X-API-Key": "case-secret",
                            "X-Trace": "keep-positive",
                        },
                    },
                },
            ],
            "workflow_steps": [],
        }
    )

    api_result = result["api_execution_result"]
    auth_headers = calls[0]["headers"] or {}

    assert len(calls) == 2
    assert auth_headers == {"X-Trace": "keep-me"}
    assert calls[1]["headers"]["Authorization"] == "Bearer real-token"
    assert calls[1]["headers"]["Cookie"] == "sid=secret"
    assert "authorization" not in calls[1]["headers"]
    assert "X-Token" not in calls[1]["headers"]
    assert calls[1]["headers"]["X-API-Key"] == "case-secret"
    assert calls[1]["headers"]["X-Trace"] == "keep-positive"
    assert api_result["passed"] == 2
    assert api_result["failed"] == 0
    assert api_result["results"][0]["category"] == "AUTH"
    assert api_result["results"][0]["passed"] is True
    assert api_result["results"][0]["envelope_status_code"] == 401


@pytest.mark.asyncio
async def test_api_runner_executes_safe_write_agent_action_crud_chain(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        def __init__(self, payload: dict) -> None:
            self._payload = payload
            self.text = json.dumps(payload)
            self.content = self.text.encode()

        def json(self) -> dict:
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            calls.append({"method": method, "url": url, **kwargs})
            if method == "POST":
                assert kwargs["json"]["name"].startswith("TestClaw_item_")
                return FakeResponse({"code": 200, "data": {"id": 77, "name": kwargs["json"]["name"]}})
            if url.endswith("/items/list"):
                return FakeResponse({"code": 200, "rows": [{"id": 77, "name": "TestClaw_item_1"}]})
            if method == "GET" and url.endswith("/items/77"):
                return FakeResponse({"code": 200, "data": {"id": 77, "name": "TestClaw_item_1"}})
            if method == "PUT":
                assert kwargs["json"]["id"] == "77"
                return FakeResponse({"code": 200, "data": {"id": 77, "name": kwargs["json"]["name"]}})
            if method == "DELETE" and url.endswith("/items/77"):
                return FakeResponse({"code": 200, "data": None})
            return FakeResponse({"code": 404})

    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(api_runner.settings, "API_MAX_EXECUTED_REQUESTS", 120)

    tool_plan = [
        {
            "tool_name": "api.safe_write_gate",
            "inputs": {"pre_call_check": "confirm_test_environment_and_unique_prefix"},
            "safety_constraints": ["require_test_env_confirmation", "generate_unique_prefix"],
        },
        {
            "tool_name": "api.http_request",
            "inputs": {"method": "POST", "path": "/items", "body": {"name": "TestClaw_item_1"}},
            "safety_constraints": ["write_allowed", "unique_prefix"],
        },
        {
            "tool_name": "api.http_request",
            "inputs": {"method": "GET", "path": "/items/list", "params": {"name": "TestClaw_item_1"}},
            "safety_constraints": ["safe_read_only"],
        },
        {
            "tool_name": "api.http_request",
            "inputs": {"method": "GET", "path": "/items/{id}", "path_params": {"id": "<id_from_post>"}},
            "safety_constraints": ["safe_read_only"],
        },
        {
            "tool_name": "api.http_request",
            "inputs": {
                "method": "PUT",
                "path": "/items",
                "body": {"id": "<id_from_post>", "name": "TestClaw_item_2"},
            },
            "safety_constraints": ["write_allowed", "unique_prefix"],
        },
        {
            "tool_name": "api.http_request",
            "inputs": {"method": "DELETE", "path": "/items/{id}", "path_params": {"id": "<id_from_post>"}},
            "safety_constraints": ["write_allowed", "require_cleanup"],
        },
    ]

    result = await api_runner.run(
        {
            "test_type": "api",
            "target_url": "https://api.example.test",
            "api_execution_policy": "write_allowed",
            "parsed_api_schema": [
                {"method": "POST", "path": "/items", "response_status": "200"},
                {"method": "GET", "path": "/items/list", "response_status": "200"},
                {
                    "method": "GET",
                    "path": "/items/{id}",
                    "path_params": [{"name": "id", "schema": {"type": "integer"}}],
                    "response_status": "200",
                },
                {"method": "PUT", "path": "/items", "response_status": "200"},
                {
                    "method": "DELETE",
                    "path": "/items/{id}",
                    "path_params": [{"name": "id", "schema": {"type": "integer"}}],
                    "response_status": "200",
                },
            ],
            "agent_strategy_decision": {
                "source": "llm",
                "intent": "api_focused_endpoints",
                "coverage_scope": "focused_documented_endpoints",
                "method_policy": {"allowed_methods": ["GET", "POST", "PUT", "DELETE"], "write_allowed": True},
                "endpoint_selection": {"source": "model_focus", "include": []},
                "tool_plan": tool_plan,
            },
            "workflow_steps": [],
        }
    )

    api_result = result["api_execution_result"]

    assert [call["method"] for call in calls] == ["POST", "GET", "GET", "PUT", "DELETE"]
    assert calls[2]["url"].endswith("/items/77")
    assert calls[4]["url"].endswith("/items/77")
    assert api_result["request_selection"]["source"] == api_runner.AGENT_ACTION_HTTP_REQUEST_SOURCE
    assert api_result["executed"] == 5
    assert api_result["passed"] == 5
    assert api_result["failed"] == 0
    assert api_result["skipped"] == 0


@pytest.mark.asyncio
async def test_api_runner_accepts_equivalent_safe_write_gate_terms(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        def __init__(self, payload: dict) -> None:
            self._payload = payload
            self.text = json.dumps(payload)
            self.content = self.text.encode()

        def json(self) -> dict:
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            calls.append({"method": method, "url": url, **kwargs})
            if method == "POST":
                assert kwargs["json"]["brandName"].startswith("TestClaw_Brand_")
                return FakeResponse({"code": 200, "data": {"id": 88, **kwargs["json"]}})
            if method == "GET" and url.endswith("/itemBrand/list"):
                return FakeResponse({"code": 200, "rows": [{"id": 88, "brandName": "TestClaw_Brand_88"}]})
            if method == "GET" and url.endswith("/itemBrand/88"):
                return FakeResponse({"code": 200, "data": {"id": 88, "brandName": "TestClaw_Brand_88"}})
            if method == "PUT":
                assert kwargs["json"]["brandId"] == "88"
                return FakeResponse({"code": 200, "data": kwargs["json"]})
            if method == "DELETE" and url.endswith("/itemBrand/88"):
                return FakeResponse({"code": 200, "data": None})
            return FakeResponse({"code": 404})

    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(api_runner.settings, "API_MAX_EXECUTED_REQUESTS", 120)

    result = await api_runner.run(
        {
            "test_type": "api",
            "target_url": "https://api.example.test",
            "api_execution_policy": "write_allowed",
            "parsed_api_schema": [
                {"method": "POST", "path": "/itemBrand", "response_status": "200"},
                {"method": "GET", "path": "/itemBrand/list", "response_status": "200"},
                {
                    "method": "GET",
                    "path": "/itemBrand/{id}",
                    "path_params": [{"name": "id", "schema": {"type": "integer"}}],
                    "response_status": "200",
                },
                {"method": "PUT", "path": "/itemBrand", "response_status": "200"},
                {
                    "method": "DELETE",
                    "path": "/itemBrand/{id}",
                    "path_params": [{"name": "id", "schema": {"type": "integer"}}],
                    "response_status": "200",
                },
            ],
            "agent_strategy_decision": {
                "source": "llm",
                "intent": "api_focused_endpoints",
                "coverage_scope": "focused_documented_endpoints",
                "method_policy": {"allowed_methods": ["GET", "POST", "PUT", "DELETE"], "write_allowed": True},
                "endpoint_selection": {"source": "model_focus", "include": []},
                "tool_plan": [
                    {
                        "tool_name": "api.safe_write_gate",
                        "inputs": {
                            "endpoint": "/itemBrand",
                            "method": "POST",
                            "data_prefix": "TestClaw_",
                        },
                        "safety_constraints": ["data_isolation", "cleanup_required"],
                    },
                    {
                        "tool_name": "api.http_request",
                        "inputs": {
                            "method": "POST",
                            "path": "/itemBrand",
                            "body": {"brandName": "TestClaw_Brand_88"},
                        },
                        "safety_constraints": ["use_unique_prefix"],
                    },
                    {
                        "tool_name": "api.http_request",
                        "inputs": {
                            "method": "GET",
                            "path": "/itemBrand/list",
                            "params": {"brandName": "TestClaw_Brand_88"},
                        },
                        "safety_constraints": ["read_only"],
                    },
                    {
                        "tool_name": "api.http_request",
                        "inputs": {"method": "GET", "path": "/itemBrand/{id}", "path_params": {"id": "<id_from_post>"}},
                        "safety_constraints": ["read_only"],
                    },
                    {
                        "tool_name": "api.http_request",
                        "inputs": {
                            "method": "PUT",
                            "path": "/itemBrand",
                            "body": {"brandId": "<id_from_post>", "brandName": "TestClaw_Brand_88_updated"},
                        },
                        "safety_constraints": ["use_unique_prefix"],
                    },
                    {
                        "tool_name": "api.http_request",
                        "inputs": {
                            "method": "DELETE",
                            "path": "/itemBrand/{id}",
                            "path_params": {"id": "<id_from_post>"},
                        },
                        "safety_constraints": ["cleanup_test_data"],
                    },
                ],
            },
            "workflow_steps": [],
        }
    )

    api_result = result["api_execution_result"]

    assert [call["method"] for call in calls] == ["POST", "GET", "GET", "PUT", "DELETE"]
    assert api_result["request_selection"]["source"] == api_runner.AGENT_ACTION_HTTP_REQUEST_SOURCE
    assert api_result["executed"] == 5
    assert api_result["passed"] == 5
    assert api_result["failed"] == 0
    assert api_result["skipped"] == 0


def test_api_runner_prefers_exact_action_path_over_template_match() -> None:
    schema = [
        {
            "method": "GET",
            "path": "/itemBrand/{id}",
            "path_params": [{"name": "id", "schema": {"type": "integer"}}],
            "response_status": "200",
        },
        {"method": "GET", "path": "/itemBrand/list", "response_status": "200"},
    ]
    actions = validate_agent_action_plan(
        [
            {
                "tool_name": "api.http_request",
                "inputs": {"method": "GET", "path": "/itemBrand/list"},
                "safety_constraints": ["read_only"],
            }
        ],
        parsed_api_schema=schema,
        execution_policy="write_allowed",
    )

    requests = api_runner._build_agent_action_test_requests(
        actions,
        schema,
        "https://api.example.test",
        {},
        execution_policy="write_allowed",
    )

    assert len(requests) == 1
    assert requests[0]["schema_path"] == "/itemBrand/list"
    assert requests[0]["url"] == "https://api.example.test/itemBrand/list"
    assert requests[0].get("depends_on") is None
    assert requests[0].get("path_param_dependencies") is None


@pytest.mark.asyncio
async def test_api_runner_normalizes_model_action_template_aliases_and_repairs_body(
    monkeypatch,
) -> None:
    calls = []

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        def __init__(self, payload: dict) -> None:
            self._payload = payload
            self.text = json.dumps(payload)
            self.content = self.text.encode()

        def json(self) -> dict:
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            calls.append({"method": method, "url": url, **kwargs})
            body = kwargs.get("json") or {}
            if method == "POST":
                assert body == {"brandName": "TestClaw_Model_Brand"}
                return FakeResponse({"code": 200, "data": {"id": 88, **body}})
            if method == "GET" and url.endswith("/itemBrand/list"):
                assert kwargs["params"] == {"brandName": "TestClaw_Model_Brand"}
                return FakeResponse({"code": 200, "rows": [{"id": 88, "brandName": body.get("brandName")}]})
            if method == "GET" and url.endswith("/itemBrand/88"):
                return FakeResponse({"code": 200, "data": {"id": 88, "brandName": "TestClaw_Model_Brand"}})
            if method == "PUT":
                assert body == {"brandId": "88", "brandName": "TestClaw_Model_Brand_updated"}
                return FakeResponse({"code": 200, "data": body})
            if method == "DELETE" and url.endswith("/itemBrand/88"):
                return FakeResponse({"code": 200, "data": None})
            return FakeResponse({"code": 404})

    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(api_runner.settings, "API_MAX_EXECUTED_REQUESTS", 120)

    result = await api_runner.run(
        {
            "test_type": "api",
            "target_url": "https://api.example.test",
            "api_execution_policy": "write_allowed",
            "parsed_api_schema": [
                {
                    "method": "POST",
                    "path": "/itemBrand",
                    "request_body_content_type": "application/json",
                    "request_body_schema": {
                        "type": "object",
                        "required": ["id", "brandName"],
                        "properties": {
                            "id": {"type": "integer"},
                            "brandName": {"type": "string"},
                        },
                    },
                    "response_status": "200",
                },
                {"method": "GET", "path": "/itemBrand/list", "response_status": "200"},
                {
                    "method": "GET",
                    "path": "/itemBrand/{id}",
                    "path_params": [{"name": "id", "schema": {"type": "integer"}}],
                    "response_status": "200",
                },
                {
                    "method": "PUT",
                    "path": "/itemBrand",
                    "request_body_content_type": "application/json",
                    "request_body_schema": {
                        "type": "object",
                        "required": ["brandId", "brandName"],
                        "properties": {
                            "brandId": {"type": "integer"},
                            "brandName": {"type": "string"},
                        },
                    },
                    "response_status": "200",
                },
                {
                    "method": "DELETE",
                    "path": "/itemBrand/{id}",
                    "path_params": [{"name": "id", "schema": {"type": "integer"}}],
                    "response_status": "200",
                },
            ],
            "agent_strategy_decision": {
                "source": "llm",
                "intent": "api_focused_endpoints",
                "coverage_scope": "focused_documented_endpoints",
                "method_policy": {"allowed_methods": ["GET", "POST", "PUT", "DELETE"], "write_allowed": True},
                "endpoint_selection": {"source": "model_focus", "include": []},
                "tool_plan": [
                    {
                        "tool_name": "api.safe_write_gate",
                        "inputs": {
                            "safe_write_gate": "approval",
                            "method": "POST",
                            "path": "/itemBrand",
                            "data_prefix": "TestClaw_",
                        },
                        "safety_constraints": [
                            "write_allowed",
                            "data_isolation",
                            "cleanup_required",
                        ],
                        "reason": "Approve isolated TestClaw-prefixed CRUD data with cleanup.",
                    },
                    {
                        "tool_name": "api.http_request",
                        "inputs": {
                            "method": "POST",
                            "path": "/itemBrand",
                            "body_template": {"name": "TestClaw_Model_Brand"},
                            "variable_extraction": {"created_id": "$.data.id"},
                        },
                        "safety_constraints": ["use_safe_write_gate_result", "data_prefix"],
                    },
                    {
                        "tool_name": "api.http_request",
                        "inputs": {
                            "method": "GET",
                            "path": "/itemBrand/list",
                            "params": {"brandName": "TestClaw_Model_Brand"},
                        },
                        "safety_constraints": ["read_only"],
                    },
                    {
                        "tool_name": "api.http_request",
                        "inputs": {"method": "GET", "path": "/itemBrand/${created_id}"},
                        "safety_constraints": ["read_only"],
                    },
                    {
                        "tool_name": "api.http_request",
                        "inputs": {
                            "method": "PUT",
                            "path": "/itemBrand",
                            "body_template": {
                                "brandId": "${created_id}",
                                "name": "TestClaw_Model_Brand_updated",
                            },
                        },
                        "safety_constraints": ["use_safe_write_gate_result", "data_prefix"],
                    },
                    {
                        "tool_name": "api.http_request",
                        "inputs": {"method": "DELETE", "path": "/itemBrand/${created_id}"},
                        "safety_constraints": ["use_safe_write_gate_result"],
                        "reason": "Clean up the disposable TestClaw brand record.",
                    },
                ],
            },
            "workflow_steps": [],
        }
    )

    api_result = result["api_execution_result"]

    assert [call["method"] for call in calls] == ["POST", "GET", "GET", "PUT", "DELETE"]
    assert calls[2]["url"].endswith("/itemBrand/88")
    assert calls[4]["url"].endswith("/itemBrand/88")
    assert api_result["executed"] == 5
    assert api_result["passed"] == 5
    assert api_result["failed"] == 0
    assert api_result["skipped"] == 0
    assert api_result["results"][0]["extractions"] == [
        {"name": "created_id", "path": "$.data.id", "extracted": True}
    ]
    assert api_result["results"][0]["request_body_source"] == "agent_action_schema_repair"
    assert api_result["results"][3]["request_body_source"] == "agent_action_schema_repair"


def test_api_runner_crud_body_prefix_keeps_schema_numeric_code_fields() -> None:
    body, _generation = api_runner._body_for_crud_endpoint(
        {
            "method": "POST",
            "path": "/system/dict/data",
            "request_body_content_type": "application/json",
            "request_body_schema": {
                "type": "object",
                "properties": {
                    "dictCode": {"type": "integer", "format": "int64"},
                    "dictLabel": {"type": "string"},
                    "dictType": {"type": "string"},
                    "dictValue": {"type": "string"},
                    "dictSort": {"type": "integer"},
                    "createTime": {"type": "string"},
                    "params": {
                        "type": "object",
                        "properties": {"key": {"type": "string"}},
                    },
                },
            },
        },
        method="POST",
        resource="data",
        prefix="TestClaw_data_1",
    )

    assert "dictCode" not in body
    assert "createTime" not in body
    assert "params" not in body
    assert body["dictLabel"] == "TestClaw_data_1"
    assert body["dictSort"] == 1


@pytest.mark.asyncio
async def test_api_runner_synthesizes_safe_crud_actions_when_model_falls_back_to_read_only(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        def __init__(self, payload: dict) -> None:
            self._payload = payload
            self.text = json.dumps(payload)
            self.content = self.text.encode()

        def json(self) -> dict:
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            calls.append({"method": method, "url": url, **kwargs})
            if method == "POST":
                assert kwargs["json"]["name"].startswith("TC_brands_")
                return FakeResponse({"code": 200, "data": {"id": 91, "name": kwargs["json"]["name"]}})
            if url.endswith("/brands/list"):
                return FakeResponse({"code": 200, "rows": [{"id": 91, "name": "TC_brands_1"}]})
            if method == "GET" and url.endswith("/brands/91"):
                return FakeResponse({"code": 200, "data": {"id": 91, "name": "TC_brands_1"}})
            if method == "PUT":
                assert kwargs["json"]["id"] == "91"
                assert kwargs["json"]["name"].startswith("TC_brands_")
                return FakeResponse({"code": 200, "data": kwargs["json"]})
            if method == "DELETE" and url.endswith("/brands/91"):
                return FakeResponse({"code": 200, "data": None})
            return FakeResponse({"code": 404})

    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(api_runner.settings, "API_MAX_EXECUTED_REQUESTS", 120)

    result = await api_runner.run(
        {
            "test_type": "api",
            "target_url": "https://api.example.test",
            "objective": "请执行安全 CRUD：新增-列表查询-详情-修改-删除，并清理测试数据",
            "api_execution_policy": "write_allowed",
            "parsed_api_schema": [
                {
                    "method": "POST",
                    "path": "/brands",
                    "response_status": "200",
                    "request_body_schema": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "required": ["name"],
                    },
                },
                {"method": "GET", "path": "/brands/list", "response_status": "200"},
                {
                    "method": "GET",
                    "path": "/brands/{id}",
                    "path_params": [{"name": "id", "schema": {"type": "integer"}}],
                    "response_status": "200",
                },
                {
                    "method": "PUT",
                    "path": "/brands",
                    "response_status": "200",
                    "request_body_schema": {
                        "type": "object",
                        "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
                        "required": ["id", "name"],
                    },
                },
                {
                    "method": "DELETE",
                    "path": "/brands/{id}",
                    "path_params": [{"name": "id", "schema": {"type": "integer"}}],
                    "response_status": "200",
                },
            ],
            "agent_strategy_decision": {
                "source": "llm",
                "intent": "api_read_only_coverage",
                "coverage_scope": "focused_documented_endpoints",
                "method_policy": {"allowed_methods": ["GET"], "write_allowed": True},
                "endpoint_selection": {
                    "source": "model_focus",
                    "include": [{"method": "GET", "path": "/brands/{id}"}],
                    "budget_behavior": "focused_only",
                },
                "tool_plan": [
                    {
                        "tool_name": "api.derive_schema_requests",
                        "inputs": {
                            "scope": "focused_documented_endpoints",
                            "include": [{"method": "GET", "path": "/brands/{id}"}],
                        },
                        "safety_constraints": ["schema_only", "safe_methods_only"],
                    }
                ],
            },
            "workflow_steps": [],
        }
    )

    api_result = result["api_execution_result"]

    assert [call["method"] for call in calls] == ["POST", "GET", "GET", "PUT", "DELETE"]
    assert calls[2]["url"].endswith("/brands/91")
    assert calls[4]["url"].endswith("/brands/91")
    assert api_result["request_selection"]["source"] == api_runner.AGENT_ACTION_HTTP_REQUEST_SOURCE
    assert api_result["request_selection"]["fallback_reason"] == "agent_http_action_plan"
    assert api_result["executed"] == 5
    assert api_result["passed"] == 5
    assert api_result["failed"] == 0
    assert api_result["all_passed"] is True
    assert any(action.get("source") == "local_crud_skill" for action in result["agent_actions"])


@pytest.mark.asyncio
async def test_api_runner_crud_fallback_tries_business_candidates_after_failed_create(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        def __init__(self, payload: dict) -> None:
            self._payload = payload
            self.text = json.dumps(payload)
            self.content = self.text.encode()

        def json(self) -> dict:
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            calls.append({"method": method, "url": url, **kwargs})
            assert "/system/" not in url
            if method == "POST" and url.endswith("/brands"):
                return FakeResponse({"code": 500, "msg": "brand create rejected"})
            if method == "GET" and url.endswith("/brands/list"):
                return FakeResponse({"code": 200, "rows": []})
            if method == "POST" and url.endswith("/categories"):
                return FakeResponse({"code": 200, "data": {"id": 92, "name": kwargs["json"]["name"]}})
            if method == "GET" and url.endswith("/categories/list"):
                return FakeResponse({"code": 200, "rows": [{"id": 92, "name": "TestClaw_categories_1"}]})
            if method == "GET" and url.endswith("/categories/92"):
                return FakeResponse({"code": 200, "data": {"id": 92, "name": "TestClaw_categories_1"}})
            if method == "PUT" and url.endswith("/categories"):
                assert kwargs["json"]["id"] == "92"
                return FakeResponse({"code": 200, "data": kwargs["json"]})
            if method == "DELETE" and url.endswith("/categories/92"):
                return FakeResponse({"code": 200, "data": None})
            return FakeResponse({"code": 404})

    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(api_runner.settings, "API_MAX_EXECUTED_REQUESTS", 120)

    result = await api_runner.run(
        {
            "test_type": "api",
            "target_url": "https://api.example.test",
            "objective": "请执行复杂 CRUD：新增、列表查询、详情、修改、删除，并在失败时继续尝试其它低风险资源",
            "api_execution_policy": "write_allowed",
            "parsed_api_schema": [
                {
                    "method": "POST",
                    "path": "/system/dict/data",
                    "request_body_schema": {
                        "type": "object",
                        "properties": {"dictLabel": {"type": "string"}},
                        "required": ["dictLabel"],
                    },
                },
                {"method": "GET", "path": "/system/dict/data/list"},
                {"method": "GET", "path": "/system/dict/data/{dictCode}"},
                {
                    "method": "PUT",
                    "path": "/system/dict/data",
                    "request_body_schema": {
                        "type": "object",
                        "properties": {"dictCode": {"type": "integer"}, "dictLabel": {"type": "string"}},
                        "required": ["dictCode", "dictLabel"],
                    },
                },
                {"method": "DELETE", "path": "/system/dict/data/{dictCodes}"},
                {
                    "method": "POST",
                    "path": "/brands",
                    "request_body_schema": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "required": ["name"],
                    },
                },
                {"method": "GET", "path": "/brands/list"},
                {"method": "GET", "path": "/brands/{id}"},
                {
                    "method": "PUT",
                    "path": "/brands",
                    "request_body_schema": {
                        "type": "object",
                        "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
                        "required": ["id", "name"],
                    },
                },
                {"method": "DELETE", "path": "/brands/{id}"},
                {
                    "method": "POST",
                    "path": "/categories",
                    "request_body_schema": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "required": ["name"],
                    },
                },
                {"method": "GET", "path": "/categories/list"},
                {"method": "GET", "path": "/categories/{id}"},
                {
                    "method": "PUT",
                    "path": "/categories",
                    "request_body_schema": {
                        "type": "object",
                        "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
                        "required": ["id", "name"],
                    },
                },
                {"method": "DELETE", "path": "/categories/{id}"},
            ],
            "agent_strategy_decision": {
                "source": "agent_strategy_fallback",
                "intent": "api_read_only_coverage",
                "coverage_scope": "all_documented_safe_methods",
                "method_policy": {"allowed_methods": ["GET"], "write_allowed": True},
                "endpoint_selection": {"source": "schema", "include": []},
                "tool_plan": [],
            },
            "workflow_steps": [],
        }
    )

    urls = [call["url"] for call in calls]
    assert not any("/system/dict/data" in url for url in urls)
    assert [call["method"] for call in calls] == [
        "POST",
        "GET",
        "POST",
        "GET",
        "GET",
        "PUT",
        "DELETE",
    ]
    assert urls[0].endswith("/brands")
    assert urls[2].endswith("/categories")
    assert urls[-1].endswith("/categories/92")
    resources = next(
        item["resources"]
        for item in result["agent_action_diagnostics"]
        if item.get("kind") == "synthetic_crud_resource_candidates"
    )
    assert resources == ["brands", "categories"]
    api_result = result["api_execution_result"]
    assert api_result["request_selection"]["fallback_reason"] == "agent_http_action_plan"
    assert api_result["failed"] == 1
    assert any(row["url"].endswith("/categories/92") for row in api_result["results"])


@pytest.mark.asyncio
async def test_api_runner_blocks_required_crud_when_only_high_risk_resources_exist(monkeypatch) -> None:
    calls = []

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs):
            calls.append({"method": method, "url": url, **kwargs})
            raise AssertionError("high-risk CRUD should be blocked before HTTP execution")

    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(api_runner.settings, "API_MAX_EXECUTED_REQUESTS", 120)

    result = await api_runner.run(
        {
            "test_type": "api",
            "target_url": "https://api.example.test",
            "objective": "执行 CRUD 新增、修改、删除并清理",
            "api_execution_policy": "write_allowed",
            "parsed_api_schema": [
                {"method": "POST", "path": "/orders", "response_status": "200"},
                {"method": "GET", "path": "/orders/list", "response_status": "200"},
                {
                    "method": "GET",
                    "path": "/orders/{id}",
                    "path_params": [{"name": "id", "schema": {"type": "integer"}}],
                    "response_status": "200",
                },
                {"method": "PUT", "path": "/orders", "response_status": "200"},
                {
                    "method": "DELETE",
                    "path": "/orders/{id}",
                    "path_params": [{"name": "id", "schema": {"type": "integer"}}],
                    "response_status": "200",
                },
            ],
            "agent_strategy_decision": {
                "source": "llm",
                "intent": "api_read_only_coverage",
                "coverage_scope": "focused_documented_endpoints",
                "method_policy": {"allowed_methods": ["GET"], "write_allowed": True},
                "endpoint_selection": {
                    "source": "model_focus",
                    "include": [{"method": "GET", "path": "/orders/{id}"}],
                    "budget_behavior": "focused_only",
                },
                "tool_plan": [],
            },
            "workflow_steps": [],
        }
    )

    api_result = result["api_execution_result"]

    assert calls == []
    assert api_result["request_selection"]["source"] == api_runner.AGENT_ACTION_HTTP_REQUEST_SOURCE
    assert api_result["request_selection"]["fallback_reason"] == "crud_action_chain_required_but_not_executable"
    assert api_result["executed"] == 0
    assert api_result["passed"] == 0
    assert api_result["failed"] == 1
    assert api_result["skipped"] == 1
    assert api_result["blocking_skipped"] == 1
    assert api_result["all_passed"] is False
    assert api_result["results"][0]["skip_type"] == api_runner.CRUD_SKILL_BLOCK_SKIP_TYPE


@pytest.mark.asyncio
async def test_api_runner_blocks_unsafe_write_agent_action_without_gate(monkeypatch) -> None:
    calls = []

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs):
            calls.append({"method": method, "url": url, **kwargs})
            raise AssertionError("unsafe write action should not execute")

    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(api_runner.settings, "API_MAX_EXECUTED_REQUESTS", 120)

    result = await api_runner.run(
        {
            "test_type": "api",
            "target_url": "https://api.example.test",
            "api_execution_policy": "write_allowed",
            "parsed_api_schema": [
                {"method": "POST", "path": "/items", "response_status": "200"},
            ],
            "agent_strategy_decision": {
                "source": "llm",
                "intent": "api_focused_endpoints",
                "coverage_scope": "focused_documented_endpoints",
                "method_policy": {"allowed_methods": ["POST"], "write_allowed": True},
                "endpoint_selection": {"source": "model_focus", "include": []},
                "tool_plan": [
                    {
                        "tool_name": "api.http_request",
                        "inputs": {"method": "POST", "path": "/items", "body": {"name": "unsafe"}},
                        "safety_constraints": ["write_allowed"],
                    },
                ],
            },
            "workflow_steps": [],
        }
    )

    api_result = result["api_execution_result"]

    assert calls == []
    assert api_result["request_selection"]["source"] == api_runner.AGENT_ACTION_HTTP_REQUEST_SOURCE
    assert api_result["request_selection"]["fallback_reason"] == "agent_http_action_plan_blocked_by_guardrail"
    assert api_result["executed"] == 0
    assert api_result["passed"] == 0
    assert api_result["failed"] == 1
    assert api_result["skipped"] == 1
    assert api_result["blocking_skipped"] == 1
    assert api_result["all_passed"] is False
    assert api_result["results"][0]["skip_type"] == api_runner.SAFE_WRITE_BLOCK_SKIP_TYPE
    observation = result["agent_observations"][0]
    assert observation["failure_type"] == "safe_write_blocked"
    assert observation["outputs"]["error_type"] == "safe_write_blocked"
    assert observation["outputs"]["safety_decision"] == "blocked_by_safe_write_gate"


@pytest.mark.asyncio
async def test_api_runner_persists_safe_binary_and_control_character_response_summaries(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        status_code = 200

        def __init__(self, content_type: str, text: str, content: bytes) -> None:
            self.headers = {"content-type": content_type}
            self.text = text
            self.content = content

        def json(self) -> dict:
            raise ValueError("not json")

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            calls.append({"method": method, "url": url, **kwargs})
            if url.endswith("/export"):
                return FakeResponse("application/octet-stream", "\x00raw-binary", b"\x00\x01abc")
            return FakeResponse("text/plain", "\x00hello\x07", b"\x00hello\x07")

    monkeypatch.setattr(api_runner.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(api_runner.settings, "API_MAX_EXECUTED_REQUESTS", 120)

    result = await api_runner.run(
        {
            "test_type": "api",
            "target_url": "http://api.example.test",
            "api_cases": [
                {"title": "export", "request_template": {"method": "GET", "path": "/export"}},
                {"title": "text preview", "request_template": {"method": "GET", "path": "/text"}},
            ],
            "workflow_steps": [],
        }
    )

    api_result = result["api_execution_result"]
    dumped = json.dumps(
        {
            "api_execution_result": api_result,
            "execution_result": result["execution_result"],
        },
        ensure_ascii=False,
        default=str,
    )

    assert len(calls) == 2
    assert api_result["failed"] == 0
    assert api_result["results"][0]["body"] == {
        "content_type": "application/octet-stream",
        "byte_count": 5,
        "preview": api_runner.BINARY_RESPONSE_PREVIEW,
    }
    assert api_result["results"][1]["body"] == "hello"
    assert "\x00" not in dumped
    assert "\\u0000" not in dumped
    assert "\x07" not in dumped


@pytest.mark.asyncio
async def test_reporter_surfaces_backend_validation_contract_failures() -> None:
    result = await reporter.run(
        {
            "test_type": "api",
            "api_execution_result": {
                "total": 1,
                "executed": 1,
                "passed": 0,
                "failed": 1,
                "skipped": 0,
                "results": [
                    {
                        "label": "EMPTY_BODY POST /items",
                        "method": "POST",
                        "url": "https://api.example.test/items",
                        "status_code": 200,
                        "envelope_status_code": 500,
                        "passed": False,
                        "category": "PARAM_VALIDATION",
                        "failure_type": "backend_validation_contract",
                        "failure_reason": "invalid input returned envelope 500",
                    }
                ],
            },
            "workflow_steps": [],
        }
    )

    bugs = result["final_report"]["bugs_found"]
    assert bugs[0]["title"].startswith("Backend validation contract failure")
    assert any("4xx" in item for item in result["final_report"]["recommendations"])

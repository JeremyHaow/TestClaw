import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from langchain_openai import OpenAIEmbeddings

from app.agent.nodes import api_runner, knowledge_retriever, reporter
from app.agent.nodes.ui_runner import _build_ui_case_batches
from app.agent.tool_registry import build_tool_registry, select_skills_for_state
from app.core.llm_gateway import LLMGateway
from app.core.redaction import REDACTED_VALUE
from app.models.llm_provider import LLMProvider, ProviderType
from app.services.api_auth import AuthResolution
from app.services.embedding_service import EmbeddingService, EmbeddingUnavailableError
from app.services.knowledge_service import KnowledgeService
from app.tools.mock_data import generate_mock_json_body


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

    assert {
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
    assert state["rag_retrieval"]["vector_source_count"] == 2
    assert state["rag_retrieval"]["sources"][0]["id"] == "knowledge-1"
    assert state["rag_retrieval"]["sources"][0]["mode"] == "vector"
    assert "secret-token" not in state["rag_context"]
    assert "[REDACTED]" in state["rag_context"]
    assert "rag-knowledge-retrieval" in {skill["name"] for skill in state["skill_plan"]}


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
                    "path": "/users",
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
                    "path": "/items",
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
                    "path": "/items",
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
                    "path": "/areas",
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
    assert calls[0]["url"] == "http://api.example.test/items"
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

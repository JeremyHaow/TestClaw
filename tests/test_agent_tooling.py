import pytest

from app.agent.nodes import api_runner, reporter
from app.agent.nodes.ui_runner import _build_ui_case_batches
from app.agent.tool_registry import build_tool_registry, select_skills_for_state
from app.core.redaction import REDACTED_VALUE
from app.services.api_auth import AuthResolution
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
async def test_api_runner_classifies_validation_envelope_500_as_backend_contract(monkeypatch) -> None:
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

    failed_validation = [
        item for item in result["api_execution_result"]["results"]
        if item.get("category") == "PARAM_VALIDATION" and not item.get("passed")
    ]

    assert calls
    assert failed_validation
    assert {item.get("failure_type") for item in failed_validation} == {"backend_validation_contract"}
    assert all(item.get("envelope_status_code") == 500 for item in failed_validation)


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

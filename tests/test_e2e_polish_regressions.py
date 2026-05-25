import importlib
import json
import shlex
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent.progress import build_execution_log_payload
from app.agent.analysis.auth_chain import extract_auth_chain
from app.agent.graph import _after_api_runner, _after_tc_generator, _after_ui_login
from app.agent.nodes import api_runner, planner, reporter, source_loader, tc_generator, ui_login, ui_test_planner
from app.agent.nodes.ui_runner import _build_ui_case_batches, _execute_ui_case_batches, run as ui_runner_run
from app.api.v1.runs import (
    RunCreate,
    _rerun_context_from_task,
    _resolve_run_target_url,
    _resolve_setup_instructions,
)
from app.api.v1.test_cases import (
    _build_suite_case_payloads,
    _extract_suite_ui_seed_url,
    _extract_suite_target_url,
    _suite_worker_kwargs,
)
from app.core.redaction import REDACTED_VALUE
from app.models.task import TaskStatus, TestType as TaskTestType
from app.models.test_case import TestCase as ModelTestCase
from app.schemas.task import TaskRead, parse_task_detail
from app.tools.playwright_commands import normalize_playwright_commands


def test_review_blocker_modules_and_frontend_nginx_config_are_present() -> None:
    for module in (
        "app.agent.nodes.ui_login",
        "app.agent.nodes.ui_test_planner",
        "app.agent.progress",
        "app.tools.playwright_commands",
    ):
        importlib.import_module(module)

    assert Path("docker/nginx/frontend.conf").is_file()


def test_playwright_command_normalizer_repairs_pseudo_commands_and_screenshot() -> None:
    specs = normalize_playwright_commands(
        [
            "wait 2000",
            "sleep 1",
            "pause",
            'assert snapshot contains "Dashboard"',
            'expect snapshot contains "Ready"',
            "screenshot shared.png",
            'run-code "await page.waitForTimeout(1000)"',
        ],
        include_unsupported=True,
    )

    assert [spec["command"] for spec in specs] == [
        "snapshot",
        "snapshot",
        "snapshot",
        "snapshot",
        "snapshot",
        "screenshot",
        'run-code "await page.waitForTimeout(1000)"',
    ]
    assert specs[0]["kind"] == "normalized"
    assert specs[1]["kind"] == "normalized"
    assert specs[2]["kind"] == "normalized"
    assert specs[3]["kind"] == "assert_snapshot_contains"
    assert specs[3]["expected"] == "Dashboard"
    assert specs[4]["kind"] == "assert_snapshot_contains"
    assert specs[4]["expected"] == "Ready"
    assert specs[5]["kind"] == "screenshot"
    assert specs[6]["kind"] == "command"


def test_playwright_command_normalizer_converts_snapshot_refs_for_cli_targets() -> None:
    specs = normalize_playwright_commands(
        [
            'fill [ref=e12] "admin"',
            "click [ref=e23]",
            'hover "[ref=e9]"',
            "select '[ref=country_1]' CN",
        ]
    )

    assert [spec["command"] for spec in specs] == [
        'fill e12 "admin"',
        "click e23",
        "hover e9",
        "select country_1 CN",
    ]
    assert all("snapshot [ref=...]" in spec["normalization"] for spec in specs)


def test_playwright_command_normalizer_repairs_viewport_pseudo_commands() -> None:
    specs = normalize_playwright_commands(
        [
            "set_viewport_size 375 667",
            "viewport 1280x720",
            "resize 390x844",
            'evaluate "await page.setViewportSize({ width: 1440, height: 900 })"',
        ],
        include_unsupported=True,
    )

    assert [spec["command"] for spec in specs] == [
        "resize 375 667",
        "resize 1280 720",
        "resize 390 844",
        "resize 1440 900",
    ]
    assert all(spec["kind"] == "normalized" for spec in specs)


def test_playwright_command_normalizer_repairs_run_code_page_signature() -> None:
    specs = normalize_playwright_commands(
        ["run-code \"async ({ page }) => { await page.waitForLoadState('domcontentloaded'); }\""],
        include_unsupported=True,
    )

    assert specs[0]["command"] == (
        "run-code \"async page => { await page.waitForLoadState('domcontentloaded'); }\""
    )
    assert "page argument" in specs[0]["normalization"]


def test_run_creation_uses_explicit_setup_instructions_without_objective_inference() -> None:
    assert _resolve_setup_instructions(
        RunCreate(source="https://example.test", objective="test the admin area")
    ) is None
    assert _resolve_setup_instructions(
        RunCreate(source="https://example.test", setup_instructions="use staging tenant")
    ) == "use staging tenant"
    assert _resolve_setup_instructions(
        RunCreate(source="https://example.test", login_instructions="legacy field")
    ) == "legacy field"


def test_run_creation_preserves_page_target_url_when_base_url_override_is_supplied() -> None:
    assert _resolve_run_target_url(
        "https://web.example.test/login",
        "url",
        "https://api.example.test",
    ) == "https://web.example.test/login"
    assert _resolve_run_target_url(
        "https://web.example.test/openapi.json",
        "swagger_url",
        "https://api.example.test",
    ) == "https://api.example.test"


def test_rerun_context_rehydrates_execution_log_fields_without_replaying_redacted_headers() -> None:
    execution_payload = build_execution_log_payload(
        {
            "source_input": "https://web.example.test/login",
            "input_type": "url",
            "ui_seed_url": "https://web.example.test/login",
            "base_url_override": "https://api.example.test",
            "api_cases": [{"title": "api smoke"}],
            "ui_cases": [{"title": "ui smoke"}],
            "setup_instructions": "log in as demo",
            "auth_headers": {
                "Authorization": "Bearer token",
                "X-Trace-ID": "trace-123",
                "Cookie": "session=secret",
            },
            "custom_headers": {
                "X-Tenant": "staging",
                "X-API-Key": "secret-key",
                "X-Redacted": REDACTED_VALUE,
            },
        }
    )
    task = SimpleNamespace(
        target_url="https://api.example.test",
        execution_log=json.dumps(execution_payload),
    )

    context = _rerun_context_from_task(task)

    assert context["source_input"] == "https://web.example.test/login"
    assert context["input_type"] == "url"
    assert context["ui_seed_url"] == "https://web.example.test/login"
    assert context["base_url_override"] == "https://api.example.test"
    assert context["api_cases"] == [{"title": "api smoke"}]
    assert context["ui_cases"] == [{"title": "ui smoke"}]
    assert context["setup_instructions"] == "log in as demo"
    assert context["login_instructions"] == "log in as demo"
    assert context["auth_headers"] == {"X-Trace-ID": "trace-123"}
    assert context["custom_headers"] == {"X-Tenant": "staging"}
    assert "Authorization" not in context["auth_headers"]
    assert "Cookie" not in context["auth_headers"]
    assert "X-API-Key" not in context["custom_headers"]
    assert REDACTED_VALUE not in context["custom_headers"].values()


def test_ui_login_parses_llm_structured_login_details() -> None:
    values = ui_login._parse_login_details_response(
        """
```json
{"requires_browser_setup":true,"setup_type":"login","provided_values":{"account":"admin","verification":"8888"},"notes":"user supplied values"}
```
"""
    )

    assert values == {
        "requires_browser_setup": True,
        "setup_type": "login",
        "provided_values": {"account": "admin", "verification": "8888"},
        "notes": "user supplied values",
    }


def test_ui_case_batches_add_screenshot_evidence_after_generated_actions() -> None:
    batches = _build_ui_case_batches(
        [
            {
                "title": "action case",
                "playwright_commands": [
                    "open http://example.test",
                    'click "Sign in"',
                    "sleep 1000",
                    'expect snapshot contains "Dashboard"',
                ],
            }
        ],
        "http://example.test",
    )

    commands = batches[0]["commands"]
    screenshot_sources = [
        spec.get("source_command") for spec in commands if spec.get("kind") == "screenshot"
    ]

    assert "auto screenshot after open" in screenshot_sources
    assert "auto screenshot after click" in screenshot_sources


def test_authenticated_business_cases_use_real_snapshot_actions_without_reopening_login() -> None:
    snapshot = """
### Snapshot
```yaml
- generic [ref=e1]:
  - link "Reports" [ref=e2] [cursor=pointer]
  - link "Inventory" [ref=e3] [cursor=pointer]
  - link "Orders" [ref=e4] [cursor=pointer]
  - link "Users" [ref=e5] [cursor=pointer]
  - button "Help" [ref=e6] [cursor=pointer]
```
"""

    cases = ui_test_planner._build_authenticated_business_cases(snapshot, minimum_cases=6)
    assert len(cases) >= 4
    assert all(case["requires_authenticated_context"] for case in cases)
    assert any("Inventory" in case["title"] for case in cases)
    assert any("click e3" in case["playwright_commands"] for case in cases)

    batches = _build_ui_case_batches(cases, "https://example.test/start", ["goto https://example.test/app"])
    first_commands = [spec["command"] for spec in batches[0]["commands"]]
    assert not any(command.startswith("open ") for command in first_commands)
    assert "reload" in first_commands


def test_authenticated_business_cases_include_deep_business_flows_from_snapshot() -> None:
    snapshot = """
### Snapshot
```yaml
- generic [ref=e1]:
  - searchbox "Search orders" [ref=e2]
  - button "Search" [ref=e3] [cursor=pointer]
  - link "Add order" [ref=e4] [cursor=pointer]
  - table "Orders" [ref=e5]:
    - row "Order 1001" [ref=e6]:
      - link "View" [ref=e7] [cursor=pointer]
```
"""

    cases = ui_test_planner._build_authenticated_business_cases(snapshot, minimum_cases=6)
    commands_by_operation = {
        case.get("operation_type"): case.get("playwright_commands", [])
        for case in cases
    }

    assert "search_flow" in commands_by_operation
    assert "record_drilldown_flow" in commands_by_operation
    assert "safe_form_validation_flow" in commands_by_operation
    assert any("fill e2" in command for command in commands_by_operation["search_flow"])
    assert any("click e3" in command for command in commands_by_operation["search_flow"])
    assert any(
        command.startswith("run-code")
        for command in commands_by_operation["safe_form_validation_flow"]
    )
    assert any("click e4" in command for command in commands_by_operation["safe_form_validation_flow"])


def test_conditional_ui_helper_clicks_are_bounded_and_skippable() -> None:
    close_command = ui_test_planner._conditional_click_labels_command(("取消", "返回"))
    submit_command = ui_test_planner._conditional_required_submit_command()
    drilldown_command = ui_test_planner._conditional_open_first_record_command()

    for command in (close_command, submit_command, drilldown_command):
        assert "click({ timeout: 1200 })" in command
        assert "isVisible({ timeout: 500 })" in command
        assert ".click(); return" not in command

    assert "no safe matching action" in close_command
    assert "skip submit" in submit_command
    assert "skip record open" in drilldown_command


def test_authenticated_case_batches_open_target_when_auth_context_is_missing() -> None:
    batches = _build_ui_case_batches(
        [
            {
                "title": "Prepared context navigation check",
                "requires_authenticated_context": True,
                "playwright_commands": ["snapshot", 'click "Inventory"', "screenshot"],
            }
        ],
        "https://example.test/start",
    )

    commands = [spec["command"] for spec in batches[0]["commands"] if not spec.get("skip")]
    assert commands[0] == "open https://example.test/start"
    assert 'click "Inventory"' in commands


def test_authenticated_case_batches_can_restore_login_state_before_actions() -> None:
    batches = _build_ui_case_batches(
        [
            {
                "title": "Prepared context navigation check",
                "requires_authenticated_context": True,
                "playwright_commands": ["snapshot", 'click "Inventory"', "screenshot"],
            }
        ],
        "https://example.test/start",
        [
            "open about:blank",
            'state-load "/tmp/login_state.json"',
            "goto https://example.test/app",
        ],
    )

    commands = [spec["command"] for spec in batches[0]["commands"]]
    assert commands[:3] == [
        "open about:blank",
        'state-load "/tmp/login_state.json"',
        "goto https://example.test/app",
    ]
    assert "snapshot" in commands


def test_reproducible_script_keeps_authenticated_case_commands() -> None:
    script = ui_test_planner._generate_reproducible_script(
        ["open https://example.test/login", "fill [ref=e1] \"admin\"", "click [ref=e2]", "snapshot"],
        [
            {
                "title": "Prepared context navigation check",
                "requires_authenticated_context": True,
                "playwright_commands": ["snapshot", 'click "Inventory"', "snapshot", "screenshot"],
            }
        ],
        "https://example.test/login",
    )

    assert "# === 1. Prepared context navigation check ===" in script
    assert 'click "Inventory"' in script
    assert script.count("snapshot") >= 3


@pytest.mark.asyncio
async def test_ui_runner_writes_distinct_case_screenshot_evidence(tmp_path, monkeypatch) -> None:
    counter = {"snapshot": 0}

    async def fake_run_playwright_cli_command(command: str, session: str = "default") -> dict:
        parts = shlex.split(command)
        if parts and parts[0] == "screenshot":
            path = Path(parts[parts.index("--filename") + 1] if "--filename" in parts else parts[1])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"image:{path.name}", encoding="utf-8")
            return {"status_code": 0, "stdout": "", "stderr": ""}
        if parts and parts[0] == "snapshot":
            counter["snapshot"] += 1
            return {
                "status_code": 0,
                "stdout": f"Dashboard snapshot {counter['snapshot']}",
                "stderr": "",
            }
        return {"status_code": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(
        "app.tools.playwright_tool.run_playwright_cli_command",
        fake_run_playwright_cli_command,
    )

    batches = _build_ui_case_batches(
        [
            {
                "title": "first",
                "playwright_commands": ["open http://example.test", "screenshot shared.png"],
            },
            {
                "title": "second",
                "playwright_commands": ["open http://example.test", "screenshot shared.png"],
            },
        ],
        "http://example.test",
    )

    result = await _execute_ui_case_batches(batches, "task-1", tmp_path, {"task_id": "task-1"})

    assert result["total"] == 2
    assert result["passed"] == 2
    assert len(result["screenshots"]) == 2
    assert result["screenshots"][0] != result["screenshots"][1]
    assert Path(result["screenshots"][0]).read_text(encoding="utf-8") != Path(
        result["screenshots"][1]
    ).read_text(encoding="utf-8")
    assert result["cases"][0]["screenshots"] == [result["screenshots"][0]]
    assert result["cases"][1]["screenshots"] == [result["screenshots"][1]]


def test_auto_route_runs_api_before_ui_when_base_url_is_available() -> None:
    state = {
        "input_type": "url",
        "test_type": "auto",
        "base_url_override": "http://api.example.test",
        "ui_seed_url": "http://web.example.test/login",
        "api_cases": [{"title": "base smoke"}],
    }

    assert _after_tc_generator(state) == "api_runner"
    assert _after_api_runner(state) == "ui_login"


def test_ui_login_short_circuits_to_reporter_when_required_login_is_not_verified() -> None:
    assert _after_ui_login({
        "setup_instructions": "prepare browser state",
        "setup_result": {"required": True},
        "login_verified": False,
    }) == "reporter"
    assert _after_ui_login({
        "setup_instructions": "prepare browser state",
        "setup_result": {"required": True},
        "login_verified": True,
    }) == "ui_test_planner"
    assert _after_ui_login({
        "setup_instructions": "only test read-only pages",
        "setup_result": {"required": False},
        "login_verified": None,
    }) == "ui_test_planner"
    assert _after_ui_login({"setup_instructions": None, "login_verified": None}) == "ui_test_planner"


def test_ui_login_detects_blocking_page_errors_from_snapshot_or_notes() -> None:
    assert ui_login._looks_like_blocking_page_error(
        "### Page\n- text: ThinkPHP\n- text: session_start(): No space left on device",
        None,
    )
    assert ui_login._looks_like_blocking_page_error("", "页面显示系统错误：磁盘空间不足，登录无法进行。")
    assert not ui_login._looks_like_blocking_page_error(
        "### Snapshot\n- textbox \"username\"\n- textbox \"password\"\n- button \"login\"",
        "login form is ready",
    )


@pytest.mark.asyncio
async def test_source_loader_extracts_base_url_from_raw_openapi_servers() -> None:
    document = {
        "openapi": "3.0.0",
        "info": {"title": "Example", "version": "1.0.0"},
        "servers": [{"url": "https://api.example.test/v1"}],
        "paths": {
            "/ping": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {"application/json": {"schema": {"type": "object"}}},
                        }
                    }
                }
            }
        },
    }
    state = {
        "source_input": json.dumps(document),
        "input_type": "swagger_json",
        "target_url": json.dumps(document),
        "workflow_steps": [],
    }

    result = await source_loader.run(state)

    assert result["target_url"] == "https://api.example.test/v1"
    assert result["parsed_api_schema"]


@pytest.mark.asyncio
async def test_source_loader_keeps_ui_page_target_when_base_url_override_is_supplied(monkeypatch) -> None:
    class FakeResponse:
        headers = {"content-type": "text/html"}
        text = "<html><title>Login</title></html>"

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, url: str) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(source_loader.httpx, "AsyncClient", FakeAsyncClient)

    state = {
        "source_input": "https://web.example.test/login",
        "input_type": "url",
        "target_url": "https://web.example.test/login",
        "base_url_override": "https://api.example.test",
        "workflow_steps": [],
    }

    result = await source_loader.run(state)

    assert result["target_url"] == "https://web.example.test/login"
    assert result["ui_seed_url"] == "https://web.example.test/login"
    assert result["base_url_override"] == "https://api.example.test"


@pytest.mark.asyncio
async def test_source_loader_preserves_suite_api_target_with_ui_seed(monkeypatch) -> None:
    calls = []

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, url: str) -> None:
            calls.append(url)
            raise AssertionError("suite source should not be fetched as a normal URL")

    monkeypatch.setattr(source_loader.httpx, "AsyncClient", FakeAsyncClient)

    state = {
        "source_input": "suite",
        "input_type": "url",
        "target_url": "https://api.example.test",
        "ui_seed_url": "https://web.example.test",
        "workflow_steps": [],
    }

    result = await source_loader.run(state)

    assert result["target_url"] == "https://api.example.test"
    assert result["ui_seed_url"] == "https://web.example.test"
    assert calls == []


def test_source_loader_rewrites_ruoyi_dev_proxy_paths_from_api_docs_url() -> None:
    endpoints = [
        {"method": "GET", "path": "/dev-api/wms/area/{id}"},
        {"method": "POST", "path": "/dev-api/wms/area/list"},
    ]

    rewrite = source_loader._infer_proxy_prefix_rewrite(
        "http://60.204.225.104/api/v3/api-docs",
        endpoints,
    )
    rewritten = source_loader._apply_path_prefix_rewrite(endpoints, rewrite)

    assert rewrite == {"from": "/dev-api", "to": "/api"}
    assert rewritten[0]["path"] == "/api/wms/area/{id}"
    assert rewritten[0]["original_path"] == "/dev-api/wms/area/{id}"


def test_auth_chain_uses_openapi_security_auth_required_flag() -> None:
    chain = extract_auth_chain([
        {"method": "GET", "path": "/api/wms/area/{id}", "auth_required": True},
        {"method": "GET", "path": "/api/public/ping", "auth_required": False},
    ])

    assert chain.auth_type == "bearer"
    assert chain.credentials[0].name == "Authorization"
    assert chain.credentials[0].consumed_by == ["GET /api/wms/area/{id}"]


@pytest.mark.asyncio
async def test_api_runner_uses_base_url_override_for_fallback_request(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        status_code = 200
        text = '{"ok":true}'

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

    state = {
        "test_type": "api",
        "target_url": "",
        "base_url_override": "http://api.example.test",
        "workflow_steps": [],
    }

    result = await api_runner.run(state)

    assert calls == [
        {
            "method": "GET",
            "url": "http://api.example.test",
            "headers": None,
            "json": None,
            "params": None,
        }
    ]
    assert result["api_execution_result"]["total"] == 1
    assert result["api_execution_result"]["completed"] == 1


@pytest.mark.asyncio
async def test_api_runner_uses_suite_request_template_and_redacts_headers(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        status_code = 200
        text = '{"ok":true}'

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

    state = {
        "test_type": "api",
        "target_url": "https://api.example.test",
        "auth_headers": {"Authorization": "Bearer auth-secret"},
        "api_cases": [
            {
                "title": "suite smoke",
                "category": "SMOKE",
                "test_data": {
                    "request_template": {
                        "method": "GET",
                        "path": "/health",
                        "headers": {
                            "X-API-Key": "case-secret",
                            "Cookie": "session=secret",
                        },
                    }
                },
            }
        ],
        "workflow_steps": [],
    }

    result = await api_runner.run(state)

    assert calls[0]["url"] == "https://api.example.test/health"
    assert calls[0]["headers"]["Authorization"] == "Bearer auth-secret"
    assert calls[0]["headers"]["X-API-Key"] == "case-secret"

    persisted_result = result["api_execution_result"]["results"][0]
    assert persisted_result["request_headers"]["Authorization"] == REDACTED_VALUE
    assert persisted_result["request_headers"]["X-API-Key"] == REDACTED_VALUE
    assert persisted_result["request_headers"]["Cookie"] == REDACTED_VALUE
    assert "auth-secret" not in json.dumps(result["api_execution_result"])
    assert "case-secret" not in json.dumps(result["execution_result"])


@pytest.mark.asyncio
async def test_api_runner_safe_policy_skips_writes_and_auth_positive_without_token(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        status_code = 200
        text = '{"code":401,"msg":"认证失败"}'

        def json(self) -> dict:
            return {"code": 401, "msg": "认证失败"}

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

    state = {
        "test_type": "api",
        "target_url": "http://wms.example.test",
        "api_execution_policy": "safe_read_only",
        "parsed_api_schema": [
            {
                "method": "GET",
                "path": "/api/wms/area/{id}",
                "path_params": [{"name": "id", "schema": {"type": "integer"}}],
                "auth_required": True,
                "response_status": "200",
            },
            {
                "method": "POST",
                "path": "/api/wms/area",
                "auth_required": True,
                "request_body_content_type": "application/json",
                "example_request": {"areaName": "string"},
                "response_status": "200",
            },
        ],
        "workflow_steps": [],
    }

    result = await api_runner.run(state)

    assert calls == [
        {
            "method": "GET",
            "url": "http://wms.example.test/api/wms/area/1",
            "headers": None,
            "json": None,
            "params": {},
        }
    ]
    api_result = result["api_execution_result"]
    assert api_result["total"] == 3
    assert api_result["executed"] == 1
    assert api_result["passed"] == 1
    assert api_result["failed"] == 0
    assert api_result["skipped"] == 2
    assert api_result["all_passed"] is True
    assert any(item.get("skip_reason") for item in api_result["results"])


def test_execution_log_redacts_sensitive_headers_before_persist_and_render() -> None:
    state = {
        "api_cases": [
            {
                "title": "auth smoke",
                "request_template": {
                    "method": "GET",
                    "url": "https://api.example.test/me",
                    "headers": {
                        "Authorization": "Bearer case-secret",
                        "Proxy-Authorization": "Basic proxy-secret",
                        "X-API-Key": "x-secret",
                        "Api-Key": "api-secret",
                        "X-Session-Token": "token-secret",
                        "Cookie": "sid=cookie-secret",
                        "Content-Type": "application/json",
                    },
                },
            }
        ],
        "api_execution_result": {
            "results": [
                {
                    "request_headers": {
                        "Authorization": "Bearer result-secret",
                        "Cookie": "sid=result-cookie",
                        "X-Csrf-Token": "csrf-secret",
                    }
                }
            ]
        },
    }

    payload = build_execution_log_payload(state)
    dumped = json.dumps(payload)

    for secret in (
        "case-secret",
        "proxy-secret",
        "x-secret",
        "api-secret",
        "token-secret",
        "cookie-secret",
        "result-secret",
        "result-cookie",
        "csrf-secret",
    ):
        assert secret not in dumped

    assert payload["api_cases"][0]["request_template"]["headers"]["Authorization"] == REDACTED_VALUE
    assert payload["api_cases"][0]["request_template"]["headers"]["Content-Type"] == "application/json"

    task = SimpleNamespace(
        id="task-1",
        objective="auth smoke",
        target_url="https://api.example.test",
        status=TaskStatus.SUCCEEDED,
        test_type=TaskTestType.API,
        retry_count=0,
        generated_code=None,
        execution_log=json.dumps(state),
        api_doc_id=None,
        environment_id=None,
        created_at=datetime.utcnow(),
    )
    detail = parse_task_detail(task)
    assert "case-secret" not in detail["execution_log"]
    assert detail["api_execution_result"]["results"][0]["request_headers"]["Cookie"] == REDACTED_VALUE

    read_model = TaskRead.model_validate(task)
    assert "case-secret" not in (read_model.execution_log or "")
    assert "result-secret" not in (read_model.execution_log or "")


def test_suite_case_payloads_normalize_categories_and_hoist_templates() -> None:
    api_case = ModelTestCase(
        title="upper api",
        category="API",
        priority="P1",
        steps=[],
        expected=[],
        test_data={
            "request_template": {
                "method": "GET",
                "base_url": "https://api.example.test",
                "path": "/health",
            }
        },
    )
    smoke_case = ModelTestCase(
        title="generated smoke",
        category="SMOKE",
        priority="P1",
        steps=[],
        expected=[],
        test_data={
            "request_template": {
                "method": "POST",
                "url": "https://api.example.test/login",
                "body": {"username": "demo"},
            }
        },
    )
    page_case = ModelTestCase(
        title="page load",
        category="PAGE_LOAD",
        priority="P1",
        steps=[],
        expected=[],
        test_data={"playwright_commands": ["open https://web.example.test", "snapshot"]},
    )

    api_cases, ui_cases = _build_suite_case_payloads([api_case, smoke_case, page_case])

    assert [case["case_type"] for case in api_cases] == ["api", "api"]
    assert [case["case_type"] for case in ui_cases] == ["ui"]
    assert api_cases[0]["request_template"]["path"] == "/health"
    assert api_cases[1]["request_template"]["url"] == "https://api.example.test/login"
    assert ui_cases[0]["playwright_commands"][0] == "open https://web.example.test"
    assert _extract_suite_target_url(api_cases, ui_cases) == "https://api.example.test"
    assert _extract_suite_ui_seed_url(ui_cases) == "https://web.example.test"


def test_suite_worker_kwargs_pass_cases_and_ui_metadata_to_delay_path() -> None:
    api_cases = [{"title": "suite api", "request_template": {"url": "https://api.test/health"}}]
    ui_cases = [{"title": "suite ui", "playwright_commands": ["open https://web.test"]}]

    kwargs = _suite_worker_kwargs("auto", api_cases, ui_cases)

    assert kwargs["test_type"] == "auto"
    assert kwargs["source_input"] == "suite"
    assert kwargs["api_cases"] is api_cases
    assert kwargs["ui_cases"] is ui_cases
    assert kwargs["ui_seed_url"] == "https://web.test"
    assert kwargs["input_type"] == "url"


def test_mixed_suite_routes_api_then_ui_with_preserved_suite_cases() -> None:
    state = {
        "test_type": "auto",
        "input_type": "url",
        "api_cases": [{"title": "suite api"}],
        "ui_cases": [{"title": "suite ui"}],
        "ui_seed_url": "https://web.test",
    }

    assert _after_tc_generator(state) == "api_runner"
    assert _after_api_runner(state) == "ui_login"


@pytest.mark.asyncio
async def test_reporter_uses_actual_execution_counts_not_plan_only() -> None:
    state = {
        "test_type": "auto",
        "api_plan": {"title": "API plan"},
        "ui_plan": {"title": "UI plan"},
        "api_cases": [{"title": "base smoke"}],
        "ui_cases": [{"title": "login"}],
        "api_execution_result": {
            "total": 1,
            "passed": 1,
            "failed": 0,
            "results": [{"label": "SMOKE GET /", "passed": True}],
            "all_passed": True,
        },
        "ui_execution_result": {
            "total": 1,
            "passed": 1,
            "failed": 0,
            "cases": [{"title": "login", "passed": True}],
            "commands": [
                {
                    "command": "wait 2000",
                    "normalized_command": "snapshot",
                    "status": "executed",
                    "status_code": 0,
                    "passed": True,
                    "normalization": "Converted unsupported wait command to snapshot.",
                }
            ],
            "normalization_warnings": [
                {"source_command": "wait 2000", "detail": "Converted wait command."}
            ],
            "all_passed": True,
        },
        "workflow_steps": [],
    }

    result = await reporter.run(state)
    report = result["final_report"]

    assert report["api_test_summary"]["total"] == 1
    assert report["api_test_summary"]["executed"] == 1
    assert report["api_test_summary"]["has_execution"] is True
    assert report["ui_test_summary"]["total"] == 1
    assert report["ui_test_summary"]["executed"] == 1
    assert report["ui_test_summary"]["has_execution"] is True
    assert report["overall_verdict"] == "PASS"
    assert "API 测试执行 1 个请求" in report["summary"]
    assert "1 个通过、0 个失败" in report["summary"]
    assert "所有已执行检查均通过" in report["summary"]
    assert not report["recommendations"]


@pytest.mark.asyncio
async def test_reporter_does_not_create_product_findings_from_plan_only_state() -> None:
    state = {
        "test_type": "auto",
        "api_plan": {"title": "API plan"},
        "api_cases": [{"title": "planned smoke"}],
        "workflow_steps": [],
    }

    result = await reporter.run(state)
    report = result["final_report"]

    assert report["overall_verdict"] == "NOT_EXECUTED"
    assert report["api_test_summary"]["total"] == 0
    assert report["api_test_summary"]["executed"] == 0
    assert report["api_test_summary"]["has_execution"] is False
    assert report["api_test_summary"]["planned_cases"] == 1
    assert report["bugs_found"] == []
    assert len(report["recommendations"]) == 1
    assert "没有执行 API 请求" in report["recommendations"][0]
    assert "base URL" in report["recommendations"][0]


@pytest.mark.asyncio
async def test_planner_gates_combined_plan_for_ui_and_api_modes() -> None:
    ui_state = await planner.run(
        {
            "objective": "test ui",
            "target_url": "https://web.example.test",
            "test_type": "ui",
            "input_type": "url",
            "workflow_steps": [],
        }
    )
    assert ui_state["api_plan"] is None
    assert ui_state["ui_plan"] is not None
    assert all(plan["test_type"] == "ui" for plan in ui_state["test_plan"])

    api_state = await planner.run(
        {
            "objective": "test api",
            "target_url": "https://api.example.test",
            "test_type": "api",
            "input_type": "swagger_url",
            "workflow_steps": [],
        }
    )
    assert api_state["ui_plan"] is None
    assert api_state["api_plan"] is not None
    assert all(plan["test_type"] == "api" for plan in api_state["test_plan"])


@pytest.mark.asyncio
async def test_tc_generator_gates_cases_for_ui_and_api_modes() -> None:
    schema = [{"method": "GET", "path": "/health", "summary": "health"}]

    ui_state = await tc_generator.run(
        {
            "test_type": "ui",
            "input_type": "url",
            "target_url": "https://web.example.test",
            "ui_plan": {"title": "UI"},
            "api_plan": {"title": "API"},
            "parsed_api_schema": schema,
            "workflow_steps": [],
        }
    )
    assert ui_state["api_cases"] == []
    assert ui_state["ui_cases"]
    assert all(case.get("case_type") == "ui" for case in ui_state["test_cases"])

    api_state = await tc_generator.run(
        {
            "test_type": "api",
            "input_type": "swagger_url",
            "target_url": "https://api.example.test",
            "ui_plan": {"title": "UI"},
            "api_plan": {"title": "API"},
            "parsed_api_schema": schema,
            "workflow_steps": [],
        }
    )
    assert api_state["ui_cases"] == []
    assert api_state["api_cases"]
    assert all(case.get("case_type") == "api" for case in api_state["test_cases"])


@pytest.mark.asyncio
async def test_tc_generator_preserves_suite_selected_api_and_ui_cases() -> None:
    api_cases = [{"title": "selected api", "request_template": {"path": "/health"}}]
    ui_cases = [{"title": "selected ui", "playwright_commands": ["open https://web.test"]}]

    result = await tc_generator.run(
        {
            "test_type": "auto",
            "input_type": "url",
            "target_url": "https://api.test",
            "ui_seed_url": "https://web.test",
            "api_cases": api_cases,
            "ui_cases": ui_cases,
            "workflow_steps": [],
        }
    )

    assert result["api_cases"] == api_cases
    assert result["ui_cases"] == ui_cases
    assert result["test_cases"] == api_cases + ui_cases


@pytest.mark.asyncio
async def test_ui_test_planner_preserves_suite_selected_ui_cases(monkeypatch) -> None:
    calls = []

    async def fake_run_playwright_cli_command(command: str) -> dict:
        calls.append(command)
        if command == "snapshot":
            return {"status_code": 0, "stdout": "- button \"Suite action\" [ref=e1]", "stderr": ""}
        return {"status_code": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(
        "app.tools.playwright_tool.run_playwright_cli_command",
        fake_run_playwright_cli_command,
    )

    selected_ui_cases = [
        {
            "title": "selected ui",
            "playwright_commands": ["open https://web.test", "snapshot"],
        }
    ]

    result = await ui_test_planner.run(
        {
            "test_type": "auto",
            "input_type": "url",
            "target_url": "https://api.test",
            "ui_seed_url": "https://web.test",
            "ui_cases": selected_ui_cases,
            "workflow_steps": [],
        }
    )

    assert result["ui_cases"] == selected_ui_cases
    assert "open https://web.test" in calls
    assert "open https://api.test" not in calls
    assert "# Target: https://web.test" in result["ui_reproducible_script"]


@pytest.mark.asyncio
async def test_ui_test_planner_stops_when_required_login_was_not_verified() -> None:
    state = {
        "task_id": "task-1",
        "target_url": "https://web.example.test",
        "test_type": "ui",
        "setup_instructions": "prepare browser state",
        "setup_result": {"required": True},
        "login_verified": False,
        "login_verification_reason": "Still appears to need setup",
        "workflow_steps": [],
    }

    result = await ui_test_planner.run(state)

    assert result.get("ui_cases") in (None, [])
    assert result["workflow_steps"][-1]["node"] == "ui_test_planner"
    assert result["workflow_steps"][-1]["status"] == "failed"


@pytest.mark.asyncio
async def test_ui_runner_marks_authenticated_cases_skipped_when_login_verification_failed() -> None:
    state = {
        "task_id": "task-1",
        "target_url": "https://web.example.test",
        "test_type": "ui",
        "setup_instructions": "prepare browser state",
        "setup_result": {"required": True},
        "login_verified": False,
        "login_verification_reason": "Still appears to need setup",
        "ui_cases": [{"title": "Open dashboard", "playwright_commands": ["open https://web.example.test/dashboard"]}],
        "workflow_steps": [],
    }

    result = await ui_runner_run(state)
    ui_result = result["ui_execution_result"]

    assert ui_result["completed"] == 0
    assert ui_result["failed"] == 1
    assert ui_result["cases"][0]["status"] == "skipped"
    assert "setup" in ui_result["skip_reason"].lower()
    assert result["workflow_steps"][-1]["node"] == "ui_runner"
    assert result["workflow_steps"][-1]["status"] == "failed"


@pytest.mark.asyncio
async def test_reporter_marks_ui_only_runs_as_api_not_applicable_and_surfaces_setup_failure() -> None:
    result = await reporter.run(
        {
            "test_type": "ui",
            "setup_instructions": "prepare browser state",
            "setup_result": {"required": True},
            "login_verified": False,
            "login_verification_reason": "Still appears to need setup",
            "ui_cases": [{"title": "Open dashboard"}],
            "ui_execution_result": {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "cases": [],
                "commands": [],
                "normalization_warnings": [],
            },
            "workflow_steps": [],
        }
    )
    report = result["final_report"]

    assert report["overall_verdict"] == "FAIL"
    assert any(
        "UI 测试运行" in item and "不适用" in item
        for item in report["api_test_summary"]["key_findings"]
    )
    assert any("UI 前置准备失败" in item for item in report["ui_test_summary"]["key_findings"])
    assert any("setup verification failed" in bug["title"].lower() for bug in report["bugs_found"])
    assert report["recommendations"]

import json

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import runs
from app.main import app
from app.services import api_auth, auth_preflight_service


def _token(client: TestClient) -> str:
    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "testclaw123"})
    assert login.status_code == 200
    return login.json()["access_token"]


def test_run_preflight_classifies_raw_openapi_and_reports_readiness() -> None:
    openapi = {
        "openapi": "3.0.0",
        "info": {"title": "Demo API", "version": "1.0.0"},
        "servers": [{"url": "https://api.example.test"}],
        "paths": {
            "/health": {
                "get": {
                    "summary": "Health check",
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }

    with TestClient(app) as client:
        token = _token(client)
        response = client.post(
            "/api/v1/runs/preflight",
            json={"source": json.dumps(openapi), "test_type": "api"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["input_type"] == "swagger_json"
    assert body["test_type"] == "api"
    assert body["endpoint_count"] == 1
    assert body["estimated_executable_count"] == 1
    assert body["estimated_skipped_count"] == 0
    assert body["api_execution_policy"] == "safe_read_only"
    assert body["expected_flow"][0] == "识别输入"
    assert any(
        check["key"] == "provider" and check["status"] == "missing" for check in body["checks"]
    )
    serialized = json.dumps(body, ensure_ascii=False)
    assert "模型与 Agent" in serialized
    assert "系统设置" not in serialized
    assert "模型管理" not in serialized


def test_run_preflight_returns_structured_mission_preview(monkeypatch) -> None:
    openapi = {
        "openapi": "3.0.0",
        "info": {"title": "Mission API", "version": "1.0.0"},
        "servers": [{"url": "https://api.example.test"}],
        "paths": {
            "/health": {
                "get": {
                    "summary": "Health check",
                    "responses": {"200": {"description": "ok"}},
                }
            },
            "/items": {
                "post": {
                    "summary": "Create item",
                    "responses": {"201": {"description": "created"}},
                }
            },
        },
    }

    async def fake_worker_readiness() -> tuple[str, str, str | None]:
        return "ready", "检测到 1 个活跃 Worker", None

    monkeypatch.setattr(runs, "_best_effort_worker_readiness", fake_worker_readiness)

    with TestClient(app) as client:
        token = _token(client)
        response = client.post(
            "/api/v1/runs/preflight",
            json={
                "source": json.dumps(openapi),
                "test_type": "api",
                "objective": "验证健康检查和只读接口",
                "setup_instructions": "只允许读取测试环境数据",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    body = response.json()
    preview = body["mission_preview"]
    assert preview["target"] == "https://api.example.test"
    assert preview["input_mode"] == "Swagger/OpenAPI JSON"
    assert preview["test_mode"] == "API 检查"
    assert preview["objective"] == "验证健康检查和只读接口"
    assert "预计执行 1 个接口" in preview["scope"]
    assert "安全只读" in preview["execution_policy"]
    assert "已提供前置说明" in preview["safety_boundary"]
    assert preview["counts"]["endpoint_count"] == 2
    assert preview["counts"]["estimated_executable_count"] == 1
    assert preview["counts"]["estimated_skipped_count"] == 1
    assert preview["counts"]["flow_step_count"] == len(body["expected_flow"])
    assert isinstance(preview["correction_prompts"], list)


def test_run_preflight_requires_source() -> None:
    with TestClient(app) as client:
        token = _token(client)
        response = client.post(
            "/api/v1/runs/preflight",
            json={"source": "   "},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "source is required"


def _auth_required_openapi() -> dict:
    return {
        "openapi": "3.0.0",
        "info": {"title": "Auth API", "version": "1.0.0"},
        "servers": [{"url": "https://api.example.test"}],
        "paths": {
            "/auth/login": {
                "post": {
                    "summary": "Login",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["username", "password"],
                                    "properties": {
                                        "username": {"type": "string"},
                                        "password": {"type": "string"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"access_token": {"type": "string"}},
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/private": {
                "get": {
                    "summary": "Private endpoint",
                    "security": [{"BearerAuth": []}],
                    "responses": {"200": {"description": "ok"}},
                }
            },
        },
        "components": {
            "securitySchemes": {
                "BearerAuth": {"type": "http", "scheme": "bearer"},
            }
        },
    }


def _captcha_required_openapi() -> dict:
    document = _auth_required_openapi()
    document["paths"]["/captcha"] = {
        "get": {
            "summary": "Captcha context",
            "responses": {"200": {"description": "ok"}},
        }
    }
    login_schema = document["paths"]["/auth/login"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    login_schema["required"] = ["username", "password", "code"]
    login_schema["properties"]["code"] = {"type": "string"}
    return document


def _protected_write_only_openapi() -> dict:
    return {
        "openapi": "3.0.0",
        "info": {"title": "Write Protected API", "version": "1.0.0"},
        "servers": [{"url": "https://api.example.test"}],
        "paths": {
            "/public": {
                "get": {
                    "summary": "Public endpoint",
                    "responses": {"200": {"description": "ok"}},
                }
            },
            "/private": {
                "post": {
                    "summary": "Private write endpoint",
                    "security": [{"BearerAuth": []}],
                    "responses": {"200": {"description": "ok"}},
                }
            },
        },
        "components": {
            "securitySchemes": {
                "BearerAuth": {"type": "http", "scheme": "bearer"},
            }
        },
    }


def _check_by_key(body: dict, key: str) -> dict:
    return next(check for check in body["checks"] if check["key"] == key)


@pytest.mark.asyncio
async def test_worker_readiness_warns_when_broker_unreachable(monkeypatch) -> None:
    async def fake_broker_reachable(timeout: float) -> bool:
        return False

    monkeypatch.setattr(runs, "_redis_broker_reachable", fake_broker_reachable)

    status, detail, action = await runs._best_effort_worker_readiness()

    assert status == "warning"
    assert "Redis Broker" in detail
    assert action == "启动 Redis 和 Celery Worker 后重新预检"


def test_run_preflight_reports_worker_readiness(monkeypatch) -> None:
    openapi = {
        "openapi": "3.0.0",
        "info": {"title": "Worker API", "version": "1.0.0"},
        "servers": [{"url": "https://api.example.test"}],
        "paths": {
            "/health": {
                "get": {
                    "summary": "Health check",
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }

    async def fake_worker_readiness() -> tuple[str, str, str | None]:
        return "ready", "检测到 1 个活跃 Worker", None

    monkeypatch.setattr(runs, "_best_effort_worker_readiness", fake_worker_readiness)

    with TestClient(app) as client:
        token = _token(client)
        response = client.post(
            "/api/v1/runs/preflight",
            json={"source": json.dumps(openapi), "test_type": "api"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    worker_check = _check_by_key(response.json(), "worker")
    assert worker_check["status"] == "ready"
    assert "活跃 Worker" in worker_check["detail"]


def test_run_preflight_blocks_auth_required_api_without_credentials() -> None:
    with TestClient(app) as client:
        token = _token(client)
        response = client.post(
            "/api/v1/runs/preflight",
            json={"source": json.dumps(_auth_required_openapi()), "test_type": "api"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["auth_required_count"] == 1
    assert body["readiness"] == "blocked"
    auth_check = _check_by_key(body, "auth")
    assert auth_check["status"] == "missing"
    assert "必须提供 Token/Header" in auth_check["detail"]
    auth_prompt = next(
        prompt
        for prompt in body["mission_preview"]["correction_prompts"]
        if prompt["key"] == "auth"
    )
    assert auth_prompt["status"] == "missing"
    assert "Token" in auth_prompt["action"]


def test_run_preflight_none_confirmed_validates_direct_api_url(monkeypatch) -> None:
    calls: list[tuple[str, str, dict | None]] = []

    class FakeResponse:
        status_code = 200

        def json(self) -> dict:
            return {"url": "https://api.example.test/health"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs):
            calls.append((method, url, kwargs.get("headers")))
            return FakeResponse()

    monkeypatch.setattr(auth_preflight_service.httpx, "AsyncClient", FakeAsyncClient)

    with TestClient(app) as client:
        token = _token(client)
        response = client.post(
            "/api/v1/runs/preflight",
            json={
                "source": "https://api.example.test/health",
                "test_type": "api",
                "auth_mode": "none_confirmed",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert calls == [("GET", "https://api.example.test/health", None)]
    assert body["auth_preflight"]["status"] == "passed"
    assert body["auth_preflight"]["can_start"] is True
    assert body["auth_preflight"]["validation_results"][0]["url"] == "https://api.example.test/health"


def test_run_preflight_manual_auth_validates_direct_api_url(monkeypatch) -> None:
    calls: list[tuple[str, str, dict | None]] = []

    class FakeResponse:
        status_code = 200

        def json(self) -> dict:
            return {"ok": True}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs):
            calls.append((method, url, kwargs.get("headers")))
            return FakeResponse()

    monkeypatch.setattr(auth_preflight_service.httpx, "AsyncClient", FakeAsyncClient)

    with TestClient(app) as client:
        token = _token(client)
        response = client.post(
            "/api/v1/runs/preflight",
            json={
                "source": "https://api.example.test/private/profile",
                "test_type": "api",
                "auth_mode": "manual",
                "token": "manual-token-secret",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert calls == [
        (
            "GET",
            "https://api.example.test/private/profile",
            {"Authorization": "Bearer manual-token-secret"},
        )
    ]
    assert body["auth_preflight"]["status"] == "passed"
    assert body["auth_preflight"]["can_start"] is True
    assert "manual-token-secret" not in json.dumps(body, ensure_ascii=False)


def test_run_preflight_mission_preview_does_not_expose_manual_auth_values(monkeypatch) -> None:
    async def fake_worker_readiness() -> tuple[str, str, str | None]:
        return "ready", "检测到 1 个活跃 Worker", None

    monkeypatch.setattr(runs, "_best_effort_worker_readiness", fake_worker_readiness)

    with TestClient(app) as client:
        token = _token(client)
        response = client.post(
            "/api/v1/runs/preflight",
            json={
                "source": json.dumps(_auth_required_openapi()),
                "test_type": "api",
                "token": "manual-token-secret",
                "headers": {
                    "Authorization": "Bearer header-secret",
                    "X-Api-Key": "api-key-secret",
                    "X-Tenant": "tenant-a",
                },
                "setup_instructions": "password=setup-secret; do not delete data",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    body = response.json()
    preview = body["mission_preview"]
    serialized = json.dumps(body, ensure_ascii=False)
    assert preview["counts"]["auth_required_count"] == 1
    assert "预览不展示" in preview["auth_readiness"]
    assert "manual-token-secret" not in serialized
    assert "header-secret" not in serialized
    assert "api-key-secret" not in serialized
    assert "setup-secret" not in serialized


def test_run_preflight_auto_auth_resolves_token_without_returning_secret(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        status_code = 200
        headers = {}

        def json(self) -> dict:
            return {"data": {"token": "login-secret"}}

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

    monkeypatch.setattr(api_auth.httpx, "AsyncClient", FakeAsyncClient)

    with TestClient(app) as client:
        token = _token(client)
        response = client.post(
            "/api/v1/runs/preflight",
            json={
                "source": json.dumps(_auth_required_openapi()),
                "test_type": "api",
                "auth_config": {
                    "enabled": True,
                    "login_url": "/auth/login",
                    "body": {"username": "admin", "password": "secret"},
                    "token_path": "data.token",
                },
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == "https://api.example.test/auth/login"
    assert calls[0]["json"] == {"username": "admin", "password": "secret"}
    assert body["auth_resolved"] is True
    assert body["auth_strategy"] == "auto_login"
    assert body["auth_header_name"] == "Authorization"
    assert body["auth_preflight"]["auth_preflight_id"]
    assert body["auth_preflight"]["protected_validation_count"] == 1
    assert _check_by_key(body, "auth")["status"] == "ready"
    assert "login-secret" not in json.dumps(body)


def test_run_preflight_api_dynamic_captcha_fetches_context_without_ocr(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    class FakeCaptchaResponse:
        status_code = 200
        headers = {}
        cookies = {"captcha-session": "secret-cookie"}

        def json(self) -> dict:
            return {
                "uuid": "captcha-uuid",
                "captchaKey": "captcha-key",
                "img": "data:image/png;base64,abc",
                "captchaEnabled": True,
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, url: str, **kwargs) -> FakeCaptchaResponse:
            calls.append(("GET", url))
            return FakeCaptchaResponse()

        async def request(self, method: str, url: str, **kwargs):
            raise AssertionError(
                "API dynamic captcha preflight must not submit login without captcha text"
            )

    monkeypatch.setattr(api_auth.httpx, "AsyncClient", FakeAsyncClient)

    with TestClient(app) as client:
        token = _token(client)
        response = client.post(
            "/api/v1/runs/preflight",
            json={
                "source": json.dumps(_captcha_required_openapi()),
                "test_type": "api",
                "auth_mode": "auto",
                "captcha_mode": "dynamic",
                "auth_credentials": {"username": "admin", "password": "secret"},
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert calls == [("GET", "https://api.example.test/captcha")]
    assert body["readiness"] == "blocked"
    assert body["auth_preflight"]["missing_fields"] == ["captcha"]
    assert "不会识别图片" in body["auth_preflight"]["captcha_handling"]
    serialized = json.dumps(body, ensure_ascii=False)
    assert "secret-cookie" not in serialized


def test_run_preflight_rejects_new_auto_test_type() -> None:
    with TestClient(app) as client:
        token = _token(client)
        response = client.post(
            "/api/v1/runs/preflight",
            json={"source": "https://app.example.test", "test_type": "auto"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 400
    assert "api" in response.json()["detail"]
    assert "ui" in response.json()["detail"]


def test_run_preflight_ui_dynamic_captcha_requires_vision_model(monkeypatch) -> None:
    async def fake_worker_readiness() -> tuple[str, str, str | None]:
        return "ready", "检测到 1 个活跃 Worker", None

    async def fake_reachability(source: str) -> str:
        return "ready"

    monkeypatch.setattr(runs, "_best_effort_worker_readiness", fake_worker_readiness)
    monkeypatch.setattr(runs, "_best_effort_reachability", fake_reachability)

    with TestClient(app) as client:
        token = _token(client)
        response = client.post(
            "/api/v1/runs/preflight",
            json={
                "source": "https://app.example.test/login",
                "test_type": "ui",
                "auth_mode": "auto",
                "captcha_mode": "dynamic",
                "auth_credentials": {"username": "admin", "password": "secret"},
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["readiness"] == "blocked"
    assert body["auth_preflight"]["strategy"] == "ui_browser_login"
    assert body["auth_preflight"]["missing_fields"] == ["vision_model"]
    assert "Vision" in body["auth_preflight"]["next_action"]
    assert "模型与 Agent" in body["auth_preflight"]["next_action"]
    assert "模型管理" not in body["auth_preflight"]["next_action"]


def test_run_preflight_manual_auth_requires_protected_readonly_validation(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs):
            calls.append((method, url))
            raise AssertionError("manual auth must not validate against public read-only endpoints")

    monkeypatch.setattr(api_auth.httpx, "AsyncClient", FakeAsyncClient)

    with TestClient(app) as client:
        token = _token(client)
        response = client.post(
            "/api/v1/runs/preflight",
            json={
                "source": json.dumps(_protected_write_only_openapi()),
                "test_type": "api",
                "auth_mode": "manual",
                "token": "manual-token-secret",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert calls == []
    assert body["readiness"] == "blocked"
    assert body["auth_preflight"]["missing_fields"] == ["protected_read_only_endpoint"]
    assert "manual-token-secret" not in json.dumps(body, ensure_ascii=False)


def test_run_preflight_auto_auth_reports_missing_login_inputs() -> None:
    with TestClient(app) as client:
        token = _token(client)
        response = client.post(
            "/api/v1/runs/preflight",
            json={
                "source": json.dumps(_auth_required_openapi()),
                "test_type": "api",
                "auth_config": {"enabled": True},
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["readiness"] == "blocked"
    assert body["auth_resolved"] is False
    assert body["auth_missing_inputs"] == ["username", "password"]
    assert body["auth_required_fields"] == ["username", "password"]
    assert "登录请求体缺少必填字段" in body["auth_error"]
    assert "补充标出的基础登录凭据" in body["auth_next_action"]


def test_run_preflight_auto_auth_treats_app_level_failure_as_login_failure(monkeypatch) -> None:
    class FakeResponse:
        status_code = 200
        headers = {}

        def json(self) -> dict:
            return {"code": 500, "msg": "Password input error 1 times", "data": None}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(api_auth.httpx, "AsyncClient", FakeAsyncClient)

    with TestClient(app) as client:
        token = _token(client)
        response = client.post(
            "/api/v1/runs/preflight",
            json={
                "source": json.dumps(_auth_required_openapi()),
                "test_type": "api",
                "auth_config": {
                    "enabled": True,
                    "login_url": "/auth/login",
                    "body": {"username": "admin", "password": "secret"},
                },
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["readiness"] == "blocked"
    assert body["auth_resolved"] is False
    assert body["auth_missing_inputs"] == ["username", "password", "captcha"]
    assert body["auth_preflight"]["missing_fields"] == ["username", "password", "captcha"]
    assert "Password input error" in body["auth_error"]
    assert "登录成功" not in body["auth_error"]
    assert "token_path" not in body["auth_missing_inputs"]
    assert "Token 路径" not in (body["auth_next_action"] or "")


def test_run_preflight_auto_auth_prompts_for_token_path_when_login_has_no_token(
    monkeypatch,
) -> None:
    class FakeResponse:
        status_code = 200
        headers = {}

        def json(self) -> dict:
            return {"code": 200, "msg": "ok", "data": {"ok": True}}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(api_auth.httpx, "AsyncClient", FakeAsyncClient)

    with TestClient(app) as client:
        token = _token(client)
        response = client.post(
            "/api/v1/runs/preflight",
            json={
                "source": json.dumps(_auth_required_openapi()),
                "test_type": "api",
                "auth_config": {
                    "enabled": True,
                    "login_url": "/auth/login",
                    "body": {"username": "admin", "password": "secret"},
                },
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["auth_resolved"] is False
    assert body["auth_missing_inputs"] == ["token_path"]
    assert "登录成功，但响应中没有找到 Token" in body["auth_error"]
    assert "Token 路径" in body["auth_next_action"]


def test_run_preflight_auto_auth_extracts_cased_nested_authorization(monkeypatch) -> None:
    class FakeResponse:
        status_code = 200
        headers = {}

        def json(self) -> dict:
            return {"code": 200, "data": {"Authorization": "nested-cased-token"}}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(api_auth.httpx, "AsyncClient", FakeAsyncClient)

    with TestClient(app) as client:
        token = _token(client)
        response = client.post(
            "/api/v1/runs/preflight",
            json={
                "source": json.dumps(_auth_required_openapi()),
                "test_type": "api",
                "auth_config": {
                    "enabled": True,
                    "login_url": "/auth/login",
                    "body": {"username": "admin", "password": "secret"},
                },
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["auth_resolved"] is True
    assert body["auth_header_name"] == "Authorization"
    assert body["auth_preflight"]["protected_validation_count"] == 1
    assert "nested-cased-token" not in json.dumps(body)


def test_run_preflight_auto_auth_infers_login_url_body_and_token(monkeypatch) -> None:
    calls = []
    openapi = _auth_required_openapi()
    login_schema = openapi["paths"]["/auth/login"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    login_schema["required"] = ["userName", "password", "code", "tenantId"]
    login_schema["properties"] = {
        "userName": {"type": "string"},
        "password": {"type": "string"},
        "code": {"type": "string"},
        "tenantId": {"type": "string"},
    }

    class FakeResponse:
        status_code = 200
        headers = {}

        def json(self) -> dict:
            return {"data": {"token": "inferred-token"}}

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

    monkeypatch.setattr(api_auth.httpx, "AsyncClient", FakeAsyncClient)

    with TestClient(app) as client:
        token = _token(client)
        response = client.post(
            "/api/v1/runs/preflight",
            json={
                "source": json.dumps(openapi),
                "test_type": "api",
                "auth_config": {
                    "enabled": True,
                    "username": "admin",
                    "password": "secret",
                    "captcha": "2",
                    "tenant": "main",
                },
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert calls[0]["url"] == "https://api.example.test/auth/login"
    assert calls[0]["json"] == {
        "userName": "admin",
        "password": "secret",
        "code": "2",
        "tenantId": "main",
    }
    body = response.json()
    assert body["auth_resolved"] is True
    assert body["auth_header_name"] == "Authorization"
    assert "inferred-token" not in json.dumps(body)


def test_run_preflight_auto_auth_prefers_password_login_over_specialized_variants(
    monkeypatch,
) -> None:
    calls = []

    def login_operation(
        *,
        summary: str,
        operation_id: str,
        tags: list[str],
        required: list[str],
        properties: dict[str, dict[str, str]],
    ) -> dict:
        return {
            "summary": summary,
            "operationId": operation_id,
            "tags": tags,
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": required,
                            "properties": properties,
                        }
                    }
                },
            },
            "responses": {"200": {"description": "ok"}},
        }

    openapi = _auth_required_openapi()
    openapi["paths"] = {
        "/xcxLogin": {
            "post": login_operation(
                summary="Mini program login",
                operation_id="xcxLogin",
                tags=["wechat"],
                required=["xcxCode"],
                properties={"xcxCode": {"type": "string"}},
            )
        },
        "/smsLogin": {
            "post": login_operation(
                summary="SMS login",
                operation_id="smsLogin",
                tags=["sms"],
                required=["phone", "smsCode"],
                properties={"phone": {"type": "string"}, "smsCode": {"type": "string"}},
            )
        },
        "/emailLogin": {
            "post": login_operation(
                summary="Email login",
                operation_id="emailLogin",
                tags=["email"],
                required=["email", "emailCode"],
                properties={"email": {"type": "string"}, "emailCode": {"type": "string"}},
            )
        },
        "/oauth/token": {
            "post": login_operation(
                summary="OAuth authorization code token exchange",
                operation_id="oauthToken",
                tags=["oauth"],
                required=["grantType", "oauthCode"],
                properties={"grantType": {"type": "string"}, "oauthCode": {"type": "string"}},
            )
        },
        "/logout": {
            "post": login_operation(
                summary="Auth logout",
                operation_id="authLogout",
                tags=["auth"],
                required=["token"],
                properties={"token": {"type": "string"}},
            )
        },
        "/refreshToken": {
            "post": login_operation(
                summary="Refresh auth token",
                operation_id="refreshToken",
                tags=["auth"],
                required=["refreshToken"],
                properties={"refreshToken": {"type": "string"}},
            )
        },
        "/register": {
            "post": login_operation(
                summary="Register account",
                operation_id="register",
                tags=["auth"],
                required=["mobile", "smsCode", "password"],
                properties={
                    "mobile": {"type": "string"},
                    "smsCode": {"type": "string"},
                    "password": {"type": "string"},
                },
            )
        },
        "/captchaLogin": {
            "post": login_operation(
                summary="Captcha login",
                operation_id="captchaLogin",
                tags=["auth"],
                required=["username", "password", "captcha"],
                properties={
                    "username": {"type": "string"},
                    "password": {"type": "string"},
                    "captcha": {"type": "string"},
                },
            )
        },
        "/login": {
            "post": login_operation(
                summary="Account password login",
                operation_id="passwordLogin",
                tags=["auth"],
                required=["loginName", "pwd"],
                properties={"loginName": {"type": "string"}, "pwd": {"type": "string"}},
            )
        },
        "/private": {
            "get": {
                "summary": "Private endpoint",
                "security": [{"BearerAuth": []}],
                "responses": {"200": {"description": "ok"}},
            }
        },
    }

    class FakeResponse:
        status_code = 200
        headers = {}

        def json(self) -> dict:
            return {"data": {"token": "variant-login-token"}}

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

    monkeypatch.setattr(api_auth.httpx, "AsyncClient", FakeAsyncClient)

    with TestClient(app) as client:
        token = _token(client)
        response = client.post(
            "/api/v1/runs/preflight",
            json={
                "source": json.dumps(openapi),
                "test_type": "api",
                "auth_mode": "auto",
                "captcha_mode": "none",
                "auth_credentials": {"username": "admin", "password": "secret"},
                "auth_config": {"enabled": True},
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert calls[0]["url"] == "https://api.example.test/login"
    assert calls[0]["json"] == {"loginName": "admin", "pwd": "secret"}
    body = response.json()
    assert body["auth_resolved"] is True
    assert "variant-login-token" not in json.dumps(body)


def test_run_preflight_auto_auth_resolves_inferred_login_under_openapi_server_base(
    monkeypatch,
) -> None:
    calls = []
    openapi = _auth_required_openapi()
    openapi["servers"] = [{"url": "https://wms.qunsun.me/api"}]
    openapi["paths"] = {
        "/login": {
            "post": {
                "summary": "Account password login",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["username", "password"],
                                "properties": {
                                    "username": {"type": "string"},
                                    "password": {"type": "string"},
                                },
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "ok"}},
            }
        },
        "/private": {
            "get": {
                "summary": "Private endpoint",
                "security": [{"BearerAuth": []}],
                "responses": {"200": {"description": "ok"}},
            }
        },
    }

    class FakeResponse:
        status_code = 200
        headers = {}

        def json(self) -> dict:
            return {"data": {"token": "wms-login-token"}}

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

    monkeypatch.setattr(api_auth.httpx, "AsyncClient", FakeAsyncClient)

    with TestClient(app) as client:
        token = _token(client)
        response = client.post(
            "/api/v1/runs/preflight",
            json={
                "source": json.dumps(openapi),
                "test_type": "api",
                "auth_mode": "auto",
                "captcha_mode": "none",
                "auth_credentials": {"username": "admin", "password": "secret"},
                "auth_config": {"enabled": True},
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json()["target_url"] == "https://wms.qunsun.me/api"
    assert calls[0]["url"] == "https://wms.qunsun.me/api/login"
    assert calls[0]["json"] == {"username": "admin", "password": "secret"}
    assert calls[1]["url"] == "https://wms.qunsun.me/api/private"
    assert "wms-login-token" not in json.dumps(response.json())


def test_create_run_rejects_auth_required_api_without_token_header_or_auto_auth() -> None:
    with TestClient(app) as client:
        token = _token(client)
        response = client.post(
            "/api/v1/runs",
            json={"source": json.dumps(_auth_required_openapi()), "test_type": "api"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 400
    assert "需要鉴权" in response.json()["detail"]


def test_create_run_rejects_new_auto_test_type() -> None:
    with TestClient(app) as client:
        token = _token(client)
        response = client.post(
            "/api/v1/runs",
            json={"source": "https://app.example.test", "test_type": "auto"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 400
    assert "api" in response.json()["detail"]
    assert "ui" in response.json()["detail"]


def test_create_run_auto_auth_injects_resolved_header(monkeypatch) -> None:
    dispatched = {}

    class FakeResponse:
        status_code = 200
        headers = {}

        def json(self) -> dict:
            return {"access_token": "login-token"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            return FakeResponse()

    def fake_delay(task_id: str, objective: str, target_url: str, **kwargs) -> None:
        dispatched["task_id"] = task_id
        dispatched["objective"] = objective
        dispatched["target_url"] = target_url
        dispatched["kwargs"] = kwargs

    monkeypatch.setattr(api_auth.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(runs.run_agent_task, "delay", fake_delay)

    with TestClient(app) as client:
        token = _token(client)
        response = client.post(
            "/api/v1/runs",
            json={
                "source": json.dumps(_auth_required_openapi()),
                "test_type": "api",
                "auth_config": {
                    "enabled": True,
                    "login_url": "/auth/login",
                    "body": {"username": "admin", "password": "secret"},
                },
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert dispatched["target_url"] == "https://api.example.test"
    assert dispatched["kwargs"]["auth_headers"] == {"Authorization": "Bearer login-token"}
    assert dispatched["kwargs"]["auth_config"]["enabled"] is True


def test_create_run_reuses_matching_auth_preflight_id(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    dispatched = {}

    class FakeResponse:
        status_code = 200
        headers = {}

        def json(self) -> dict:
            return {"access_token": "cached-login-token"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
            calls.append((method, url))
            return FakeResponse()

    def fake_delay(task_id: str, objective: str, target_url: str, **kwargs) -> None:
        dispatched["kwargs"] = kwargs

    monkeypatch.setattr(api_auth.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(runs.run_agent_task, "delay", fake_delay)

    payload = {
        "source": json.dumps(_auth_required_openapi()),
        "test_type": "api",
        "auth_mode": "auto",
        "auth_config": {
            "enabled": True,
            "login_url": "/auth/login",
            "body": {"username": "admin", "password": "secret"},
        },
    }

    with TestClient(app) as client:
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        preflight = client.post("/api/v1/runs/preflight", json=payload, headers=headers)
        assert preflight.status_code == 200
        auth_preflight_id = preflight.json()["auth_preflight"]["auth_preflight_id"]
        calls.clear()
        created = client.post(
            "/api/v1/runs",
            json={**payload, "auth_preflight_id": auth_preflight_id},
            headers=headers,
        )

    assert created.status_code == 200
    assert calls == []
    assert dispatched["kwargs"]["auth_headers"] == {"Authorization": "Bearer cached-login-token"}

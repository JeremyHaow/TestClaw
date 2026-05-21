import json

from fastapi.testclient import TestClient

from app.api.v1 import runs
from app.main import app
from app.services import api_auth


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
    assert any(check["key"] == "provider" and check["status"] == "missing" for check in body["checks"])


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


def _check_by_key(body: dict, key: str) -> dict:
    return next(check for check in body["checks"] if check["key"] == key)


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
    assert _check_by_key(body, "auth")["status"] == "ready"
    assert "login-secret" not in json.dumps(body)


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


def test_run_preflight_auto_auth_prompts_for_token_path_when_login_has_no_token(monkeypatch) -> None:
    class FakeResponse:
        status_code = 200
        headers = {}

        def json(self) -> dict:
            return {"data": {"ok": True}}

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


def test_run_preflight_auto_auth_infers_login_url_body_and_token(monkeypatch) -> None:
    calls = []
    openapi = _auth_required_openapi()
    login_schema = openapi["paths"]["/auth/login"]["post"]["requestBody"]["content"]["application/json"]["schema"]
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

import json

from fastapi.testclient import TestClient

from app.api.v1 import documents as documents_api
from app.main import app


REAL_DOCS_URL = "http://60.204.225.104/api/v3/api-docs"


def _headers(client: TestClient) -> dict[str, str]:
    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "testclaw123"})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _openapi_doc(paths: dict) -> str:
    return json.dumps(
        {
            "openapi": "3.0.3",
            "info": {"title": "wms_接口文档", "version": "1.0.0"},
            "servers": [{"url": "http://60.204.225.104/api/v3"}],
            "paths": paths,
        }
    )


def test_document_url_import_preserves_source_and_raw_content(monkeypatch):
    raw_content = _openapi_doc(
        {
            "/health": {"get": {"summary": "health check", "responses": {"200": {"description": "ok"}}}},
            "/orders": {"post": {"summary": "create order", "responses": {"200": {"description": "ok"}}}},
        }
    )

    class FakeResponse:
        text = raw_content

        def raise_for_status(self) -> None:
            return None

    class FakeAsyncClient:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def get(self, url: str) -> FakeResponse:
            assert url == REAL_DOCS_URL
            return FakeResponse()

    monkeypatch.setattr(documents_api.httpx, "AsyncClient", FakeAsyncClient)

    with TestClient(app) as client:
        headers = _headers(client)
        created = client.post(
            "/api/v1/documents/import",
            json={"name": "wms_接口文档", "format": "openapi", "url": REAL_DOCS_URL},
            headers=headers,
        )

        assert created.status_code == 200
        body = created.json()
        assert body["source_url"] == REAL_DOCS_URL
        assert body["raw_content"] == raw_content
        assert len(body["parsed_endpoints"]) == 2

        fetched = client.get(f"/api/v1/documents/{body['id']}", headers=headers)
        assert fetched.status_code == 200
        assert fetched.json()["source_url"] == REAL_DOCS_URL
        assert fetched.json()["raw_content"] == raw_content


def test_document_update_raw_content_reparses_endpoints():
    initial = _openapi_doc(
        {"/health": {"get": {"summary": "health check", "responses": {"200": {"description": "ok"}}}}}
    )
    updated = _openapi_doc(
        {
            "/health": {"get": {"summary": "health check", "responses": {"200": {"description": "ok"}}}},
            "/inventory/{id}": {
                "get": {
                    "summary": "inventory detail",
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "ok"}},
                }
            },
        }
    )

    with TestClient(app) as client:
        headers = _headers(client)
        created = client.post(
            "/api/v1/documents/import",
            json={"name": "Editable API", "format": "openapi", "raw_content": initial},
            headers=headers,
        )
        assert created.status_code == 200
        assert len(created.json()["parsed_endpoints"]) == 1

        edited = client.put(
            f"/api/v1/documents/{created.json()['id']}",
            json={
                "name": "Edited API",
                "format": "openapi",
                "raw_content": updated,
                "source_url": "",
            },
            headers=headers,
        )

        assert edited.status_code == 200
        body = edited.json()
        assert body["name"] == "Edited API"
        assert body["source_url"] is None
        assert body["raw_content"] == updated
        assert len(body["parsed_endpoints"]) == 2
        assert any(endpoint["path"] == "/inventory/{id}" for endpoint in body["parsed_endpoints"])


def test_document_name_update_preserves_existing_content():
    raw_content = _openapi_doc(
        {"/health": {"get": {"summary": "health check", "responses": {"200": {"description": "ok"}}}}}
    )

    with TestClient(app) as client:
        headers = _headers(client)
        created = client.post(
            "/api/v1/documents/import",
            json={"name": "Original API", "format": "openapi", "raw_content": raw_content},
            headers=headers,
        )
        assert created.status_code == 200

        renamed = client.put(
            f"/api/v1/documents/{created.json()['id']}",
            json={"name": "Renamed API"},
            headers=headers,
        )

        assert renamed.status_code == 200
        assert renamed.json()["name"] == "Renamed API"
        assert renamed.json()["raw_content"] == raw_content
        assert len(renamed.json()["parsed_endpoints"]) == 1

import asyncio
import uuid

from fastapi.testclient import TestClient

from app.core.redaction import REDACTED_VALUE
from app.core.security import decrypt_value
from app.database import AsyncSessionLocal
from app.main import app
from app.models.environment import Environment
from app.models.knowledge import KnowledgeEntry


def _auth_headers(client: TestClient) -> dict[str, str]:
    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "testclaw123"})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_test_cases_list_paginates_and_returns_asset_metadata() -> None:
    prefix = f"case-pagination-{uuid.uuid4()}"
    with TestClient(app) as client:
        headers = _auth_headers(client)
        for index in range(3):
            response = client.post(
                "/api/v1/test-cases",
                headers=headers,
                json={
                    "title": f"{prefix}-{index}",
                    "steps": [f"GET /health/{index}"],
                    "expected": ["HTTP 200"],
                    "priority": "P1",
                    "category": "API",
                    "source": f"agent:{prefix}",
                    "test_data": {
                        "case_asset": {
                            "source_run_id": f"run-{index}",
                            "source": "api_cases",
                            "source_index": index,
                            "case_type": "api",
                        },
                        "project": "checkout",
                    },
                },
            )
            assert response.status_code == 200

        first_page = client.get(
            "/api/v1/test-cases",
            headers=headers,
            params={"page": 1, "page_size": 2, "search": prefix},
        )
        assert first_page.status_code == 200
        assert first_page.headers["x-total-count"] == "3"
        body = first_page.json()
        assert len(body) == 2
        assert body[0]["test_data"]["case_asset"]["source"] == "api_cases"
        assert body[0]["created_at"]

        second_page = client.get(
            "/api/v1/test-cases",
            headers=headers,
            params={"page": 2, "page_size": 2, "search": prefix},
        )
        assert second_page.status_code == 200
        assert second_page.headers["x-total-count"] == "3"
        assert len(second_page.json()) == 1


def test_test_cases_list_redacts_and_bounds_test_data() -> None:
    title = f"case-list-redaction-{uuid.uuid4()}"
    with TestClient(app) as client:
        headers = _auth_headers(client)
        created = client.post(
            "/api/v1/test-cases",
            headers=headers,
            json={
                "title": title,
                "steps": ["GET /profile"],
                "expected": ["HTTP 200"],
                "priority": "P1",
                "category": "API",
                "source": "manual",
                "test_data": {
                    "case_asset": {"case_type": "api", "source": "manual"},
                    "request_template": {
                        "method": "GET",
                        "path": "/profile",
                        "headers": {
                            "Authorization": "Bearer list-secret",
                            "Content-Type": "application/json",
                        },
                        "json": {"password": "body-secret"},
                    },
                    "execution_log": {"request_headers": {"Cookie": "sid=list-cookie"}},
                    "raw_content": "token=large-inline-secret",
                },
            },
        )
        assert created.status_code == 200

        response = client.get(
            "/api/v1/test-cases",
            headers=headers,
            params={"search": title, "page": 1, "page_size": 10},
        )

        assert response.status_code == 200
        dumped = response.text
        assert "list-secret" not in dumped
        assert "body-secret" not in dumped
        assert "list-cookie" not in dumped
        assert "large-inline-secret" not in dumped
        item = response.json()[0]
        assert item["test_data"]["request_template"]["headers"]["Authorization"] == REDACTED_VALUE
        assert item["test_data"]["request_template"]["headers"]["Content-Type"] == "application/json"
        assert item["test_data"]["request_template"]["json"]["password"] == REDACTED_VALUE
        assert "execution_log" not in item["test_data"]
        assert "raw_content" not in item["test_data"]


def test_knowledge_update_regenerates_embedding(monkeypatch) -> None:
    class FakeEmbeddingService:
        def __init__(self) -> None:
            self.count = 0

        async def embed_document(self, db, content: str) -> list[float]:
            self.count += 1
            return [float(self.count)]

    fake_embeddings = FakeEmbeddingService()
    monkeypatch.setattr("app.services.knowledge_service.embedding_service", fake_embeddings)

    async def load_entry(entry_id: str) -> KnowledgeEntry:
        async with AsyncSessionLocal() as session:
            entry = await session.get(KnowledgeEntry, entry_id)
            assert entry is not None
            return entry

    with TestClient(app) as client:
        headers = _auth_headers(client)
        created = client.post(
            "/api/v1/knowledge",
            headers=headers,
            json={"content": f"before {uuid.uuid4()}"},
        )
        assert created.status_code == 200
        entry_id = created.json()["id"]
        assert created.json()["embedding_available"] is True

        updated = client.put(
            f"/api/v1/knowledge/{entry_id}",
            headers=headers,
            json={"content": "after update content"},
        )
        assert updated.status_code == 200
        assert updated.json()["content"] == "after update content"
        assert updated.json()["embedding_available"] is True

    entry = asyncio.run(load_entry(entry_id))
    assert entry.content == "after update content"
    assert entry.embedding == [2.0]


def test_knowledge_update_succeeds_without_embedding_provider(monkeypatch) -> None:
    from app.services.embedding_service import EmbeddingUnavailableError

    class ToggleEmbeddingService:
        available = True

        async def embed_document(self, db, content: str) -> list[float]:
            if not self.available:
                raise EmbeddingUnavailableError("no embedding provider")
            return [1.0]

    fake_embeddings = ToggleEmbeddingService()
    monkeypatch.setattr("app.services.knowledge_service.embedding_service", fake_embeddings)

    async def load_entry(entry_id: str) -> KnowledgeEntry:
        async with AsyncSessionLocal() as session:
            entry = await session.get(KnowledgeEntry, entry_id)
            assert entry is not None
            return entry

    with TestClient(app) as client:
        headers = _auth_headers(client)
        created = client.post(
            "/api/v1/knowledge",
            headers=headers,
            json={"content": f"vector before {uuid.uuid4()}"},
        )
        assert created.status_code == 200
        assert created.json()["embedding_available"] is True
        entry_id = created.json()["id"]

        fake_embeddings.available = False
        updated = client.put(
            f"/api/v1/knowledge/{entry_id}",
            headers=headers,
            json={"content": "updated while provider unavailable"},
        )

        assert updated.status_code == 200
        assert updated.json()["content"] == "updated while provider unavailable"
        assert updated.json()["embedding_available"] is False

    entry = asyncio.run(load_entry(entry_id))
    assert entry.content == "updated while provider unavailable"
    assert entry.embedding is None


def test_environment_update_preserves_masked_variable_values() -> None:
    async def load_environment(environment_id: str) -> Environment:
        async with AsyncSessionLocal() as session:
            environment = await session.get(Environment, environment_id)
            assert environment is not None
            return environment

    with TestClient(app) as client:
        headers = _auth_headers(client)
        created = client.post(
            "/api/v1/environments",
            headers=headers,
            json={
                "name": "masked env",
                "base_url": "https://api.example.test",
                "variables": {"TOKEN": "super-secret-token"},
                "is_production": False,
            },
        )
        assert created.status_code == 200
        body = created.json()
        masked_token = body["variables"]["TOKEN"]

        updated = client.put(
            f"/api/v1/environments/{body['id']}",
            headers=headers,
            json={
                "name": "masked env edited",
                "base_url": "https://api.example.test/v2",
                "variables": {"TOKEN": masked_token},
                "is_production": False,
            },
        )
        assert updated.status_code == 200

    environment = asyncio.run(load_environment(body["id"]))
    assert decrypt_value(environment.variables_encrypted["TOKEN"]) == "super-secret-token"


def test_provider_role_defaults_are_unique_on_create_and_update() -> None:
    prefix = f"provider-default-{uuid.uuid4()}"
    with TestClient(app) as client:
        headers = _auth_headers(client)
        first = client.post(
            "/api/v1/providers",
            headers=headers,
            json={
                "name": f"{prefix}-one",
                "type": "openai",
                "api_key": "sk-one",
                "model_name": "gpt-4o-mini",
                "is_default_planner": True,
                "is_default_coder": True,
            },
        )
        assert first.status_code == 200
        assert first.json()["is_default_planner"] is True
        assert first.json()["is_default_coder"] is True
        first_id = first.json()["id"]

        second = client.post(
            "/api/v1/providers",
            headers=headers,
            json={
                "name": f"{prefix}-two",
                "type": "openai",
                "api_key": "sk-two",
                "model_name": "gpt-4o",
                "is_default_planner": True,
            },
        )
        assert second.status_code == 200
        second_id = second.json()["id"]

        try:
            providers = client.get("/api/v1/providers", headers=headers)
            assert providers.status_code == 200
            matching = [item for item in providers.json() if item["name"].startswith(prefix)]
            assert sum(1 for item in matching if item["is_default_planner"]) == 1
            assert next(item for item in matching if item["id"] == second_id)["is_default_planner"] is True
            assert next(item for item in matching if item["id"] == first_id)["is_default_coder"] is True

            updated = client.put(
                f"/api/v1/providers/{second_id}",
                headers=headers,
                json={
                    "name": f"{prefix}-two",
                    "type": "openai",
                    "model_name": "gpt-4o",
                    "is_default_planner": True,
                    "is_default_coder": True,
                },
            )
            assert updated.status_code == 200

            providers = client.get("/api/v1/providers", headers=headers)
            matching = [item for item in providers.json() if item["name"].startswith(prefix)]
            assert sum(1 for item in matching if item["is_default_planner"]) == 1
            assert sum(1 for item in matching if item["is_default_coder"]) == 1
            assert next(item for item in matching if item["id"] == second_id)["is_default_coder"] is True
        finally:
            client.delete(f"/api/v1/providers/{second_id}", headers=headers)
            client.delete(f"/api/v1/providers/{first_id}", headers=headers)

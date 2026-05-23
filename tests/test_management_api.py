import asyncio
import uuid

from fastapi.testclient import TestClient

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

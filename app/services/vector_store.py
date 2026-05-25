from __future__ import annotations

import importlib.util
import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import select

from app.config import Settings, settings
from app.core.redaction import redact_sensitive_text
from app.models.knowledge import KnowledgeEntry
from app.services.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)


_MAX_SNIPPET_CHARS = 360


@dataclass(slots=True)
class KnowledgeVectorRecord:
    id: str
    content: str
    embedding: list[float] | None
    source_script_id: str | None
    created_at: datetime | None
    raw: Any = None


@dataclass(slots=True)
class VectorSearchResult:
    status: str
    mode: str
    query: str
    match_count: int
    sources: list[dict[str, Any]]
    vector_source_count: int
    embedding_backfill_count: int = 0
    fallback_reason: str | None = None
    effect: str = ""
    backend: str = "database"
    requested_backend: str | None = None
    backend_config: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "mode": self.mode,
            "query": self.query,
            "match_count": self.match_count,
            "sources": self.sources,
            "vector_source_count": self.vector_source_count,
            "embedding_backfill_count": self.embedding_backfill_count,
            "fallback_reason": self.fallback_reason,
            "effect": self.effect,
            "backend": self.backend,
            "requested_backend": self.requested_backend or self.backend,
            "backend_config": self.backend_config or {},
        }


class KnowledgeVectorStore(Protocol):
    backend_name: str

    def backend_info(self) -> dict[str, Any]:
        ...

    async def load_recent_entries(self, db: Any, limit: int) -> list[KnowledgeVectorRecord]:
        ...

    async def similarity_search(
        self,
        *,
        db: Any,
        entries: list[KnowledgeVectorRecord],
        query: str,
        query_vector: list[float],
        embedding_client: Any,
        embedding_service: EmbeddingService,
        max_sources: int,
        min_score: float,
    ) -> VectorSearchResult:
        ...


def _safe_text(value: Any, limit: int) -> str:
    text = redact_sensitive_text(str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _entry_embedding(entry: KnowledgeVectorRecord) -> list[float] | None:
    value = entry.embedding
    if not isinstance(value, list):
        return None
    vector: list[float] = []
    try:
        for item in value:
            if isinstance(item, bool):
                return None
            vector.append(float(item))
    except (TypeError, ValueError):
        return None
    return vector or None


def _cosine_similarity(left: list[float], right: list[float]) -> float | None:
    if not left or len(left) != len(right):
        return None
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return None
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


def _created_at(entry: KnowledgeVectorRecord) -> datetime:
    return entry.created_at if isinstance(entry.created_at, datetime) else datetime.min


def _source_payload(entry: KnowledgeVectorRecord, score: float, mode: str) -> dict[str, Any]:
    return {
        "id": entry.id,
        "source_script_id": entry.source_script_id,
        "score": round(float(score), 4),
        "mode": mode,
        "snippet": _safe_text(entry.content, _MAX_SNIPPET_CHARS),
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


class DatabaseKnowledgeVectorStore:
    backend_name = "database"

    def backend_info(self) -> dict[str, Any]:
        return {
            "requested": "database",
            "active": "database",
            "storage": "knowledge_entries.embedding",
        }

    async def load_recent_entries(self, db: Any, limit: int) -> list[KnowledgeVectorRecord]:
        result = await db.execute(
            select(KnowledgeEntry).order_by(KnowledgeEntry.created_at.desc()).limit(limit)
        )
        return [
            KnowledgeVectorRecord(
                id=str(entry.id),
                content=str(entry.content or ""),
                embedding=entry.embedding if isinstance(entry.embedding, list) else None,
                source_script_id=entry.source_script_id,
                created_at=entry.created_at,
                raw=entry,
            )
            for entry in result.scalars()
        ]

    async def _backfill_missing_embeddings(
        self,
        db: Any,
        entries: list[KnowledgeVectorRecord],
        embedding_client: Any,
        embedding_service: EmbeddingService,
    ) -> tuple[int, str | None]:
        missing = [entry for entry in entries if _entry_embedding(entry) is None]
        if not missing:
            return 0, None
        try:
            vectors = await embedding_service.embed_documents_with_client(
                embedding_client,
                [entry.content for entry in missing],
            )
        except Exception as exc:
            return 0, _safe_text(exc, 180)

        stored = 0
        for entry, vector in zip(missing, vectors, strict=True):
            if not vector:
                continue
            entry.embedding = vector
            if entry.raw is not None:
                entry.raw.embedding = vector
            stored += 1

        if not stored:
            return 0, "Embedding provider returned no usable vectors for stored knowledge entries."

        try:
            await db.commit()
        except Exception as exc:
            if hasattr(db, "rollback"):
                await db.rollback()
            return 0, _safe_text(exc, 180)
        return stored, None

    async def similarity_search(
        self,
        *,
        db: Any,
        entries: list[KnowledgeVectorRecord],
        query: str,
        query_vector: list[float],
        embedding_client: Any,
        embedding_service: EmbeddingService,
        max_sources: int,
        min_score: float,
    ) -> VectorSearchResult:
        backfill_count, backfill_failure = await self._backfill_missing_embeddings(
            db,
            entries,
            embedding_client,
            embedding_service,
        )

        scored: list[tuple[float, KnowledgeVectorRecord]] = []
        vector_source_count = 0
        for entry in entries:
            vector = _entry_embedding(entry)
            if vector is None:
                continue
            similarity = _cosine_similarity(query_vector, vector)
            if similarity is None:
                continue
            vector_source_count += 1
            if similarity >= min_score:
                scored.append((similarity, entry))

        scored.sort(key=lambda item: (item[0], _created_at(item[1])), reverse=True)
        sources = [
            _source_payload(entry, score, mode="vector")
            for score, entry in scored[:max_sources]
        ]
        status = "matched" if sources else "empty"
        effect = (
            "Vector RAG selected similar prior testing notes and injected them into "
            "planner and case-generation prompts."
            if sources
            else "Vector RAG ran successfully but found no similar prior knowledge, so planning used live input only."
        )
        return VectorSearchResult(
            status=status,
            mode="vector",
            query=query,
            match_count=len(scored),
            sources=sources,
            vector_source_count=vector_source_count,
            embedding_backfill_count=backfill_count,
            fallback_reason=backfill_failure,
            effect=effect,
            backend="database",
            backend_config=self.backend_info(),
        )


class MilvusKnowledgeVectorStore:
    """Milvus-ready vector boundary with database fallback when the client is absent."""

    backend_name = "milvus"

    def __init__(
        self,
        config: Settings = settings,
        fallback: KnowledgeVectorStore | None = None,
    ) -> None:
        self.config = config
        self.fallback = fallback or DatabaseKnowledgeVectorStore()

    def backend_info(self) -> dict[str, Any]:
        dependency_available = importlib.util.find_spec("pymilvus") is not None
        uri_configured = bool(getattr(self.config, "MILVUS_URI", ""))
        return {
            "requested": "milvus",
            "active": "milvus" if dependency_available and uri_configured else "database",
            "uri_configured": uri_configured,
            "collection": getattr(self.config, "MILVUS_COLLECTION", "testclaw_knowledge"),
            "dependency_available": dependency_available,
        }

    async def load_recent_entries(self, db: Any, limit: int) -> list[KnowledgeVectorRecord]:
        return await self.fallback.load_recent_entries(db, limit)

    async def similarity_search(
        self,
        *,
        db: Any,
        entries: list[KnowledgeVectorRecord],
        query: str,
        query_vector: list[float],
        embedding_client: Any,
        embedding_service: EmbeddingService,
        max_sources: int,
        min_score: float,
    ) -> VectorSearchResult:
        backend_info = self.backend_info()
        if not backend_info["dependency_available"] or not backend_info["uri_configured"]:
            reason = (
                "Milvus vector store selected but pymilvus is not installed."
                if not backend_info["dependency_available"]
                else "Milvus vector store selected but MILVUS_URI is not configured."
            )
            result = await self.fallback.similarity_search(
                db=db,
                entries=entries,
                query=query,
                query_vector=query_vector,
                embedding_client=embedding_client,
                embedding_service=embedding_service,
                max_sources=max_sources,
                min_score=min_score,
            )
            result.requested_backend = "milvus"
            result.backend_config = {**backend_info, "fallback_reason": reason}
            result.fallback_reason = result.fallback_reason or reason
            return result

        logger.info(
            "Milvus vector backend is configured for collection %s; using database fallback until "
            "remote collection sync is enabled.",
            backend_info["collection"],
        )
        result = await self.fallback.similarity_search(
            db=db,
            entries=entries,
            query=query,
            query_vector=query_vector,
            embedding_client=embedding_client,
            embedding_service=embedding_service,
            max_sources=max_sources,
            min_score=min_score,
        )
        result.requested_backend = "milvus"
        result.backend_config = {
            **backend_info,
            "fallback_reason": "Milvus backend is configured; database fallback remains active until collection sync is enabled.",
        }
        return result


def normalize_vector_store_backend(value: str | None) -> str:
    normalized = (value or "database").strip().lower()
    aliases = {
        "local": "database",
        "json": "database",
        "pg": "database",
        "postgres": "database",
        "postgresql": "database",
        "milvus": "milvus",
    }
    return aliases.get(normalized, "database")


def get_knowledge_vector_store(config: Settings = settings) -> KnowledgeVectorStore:
    backend = normalize_vector_store_backend(getattr(config, "RAG_VECTOR_STORE_BACKEND", "database"))
    if backend == "milvus":
        return MilvusKnowledgeVectorStore(config=config)
    return DatabaseKnowledgeVectorStore()

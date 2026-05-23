from __future__ import annotations

import logging
from collections.abc import Sequence

from langchain_core.embeddings import Embeddings
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm_gateway import llm_gateway
from app.core.redaction import redact_sensitive_text

logger = logging.getLogger(__name__)

_MAX_EMBEDDING_INPUT_CHARS = 6000


class EmbeddingUnavailableError(RuntimeError):
    """Raised when no configured embedding provider can produce vectors."""


class EmbeddingService:
    def prepare_text(self, text: str | None) -> str:
        return redact_sensitive_text(str(text or "")).strip()[:_MAX_EMBEDDING_INPUT_CHARS]

    def _coerce_vector(self, vector: Sequence[float] | None) -> list[float] | None:
        if vector is None or isinstance(vector, (str, bytes)):
            return None
        coerced: list[float] = []
        try:
            for value in vector:
                if isinstance(value, bool):
                    return None
                coerced.append(float(value))
        except (TypeError, ValueError):
            return None
        return coerced or None

    async def get_client(self, db: AsyncSession) -> Embeddings:
        try:
            return await llm_gateway.get_embeddings(db)
        except Exception as exc:
            raise EmbeddingUnavailableError(str(exc)) from exc

    async def embed_query_with_client(self, client: Embeddings, text: str) -> list[float]:
        prepared = self.prepare_text(text)
        if not prepared:
            raise EmbeddingUnavailableError("No query text was available for embedding")
        vector = self._coerce_vector(await client.aembed_query(prepared))
        if vector is None:
            raise EmbeddingUnavailableError("Embedding provider returned an empty query vector")
        return vector

    async def embed_documents_with_client(
        self,
        client: Embeddings,
        texts: list[str],
    ) -> list[list[float] | None]:
        prepared_by_index = [
            (index, prepared)
            for index, text in enumerate(texts)
            if (prepared := self.prepare_text(text))
        ]
        vectors: list[list[float] | None] = [None] * len(texts)
        if not prepared_by_index:
            return vectors

        embedded = await client.aembed_documents([prepared for _, prepared in prepared_by_index])
        if len(embedded) != len(prepared_by_index):
            raise EmbeddingUnavailableError("Embedding provider returned an unexpected vector count")

        for (index, _), vector in zip(prepared_by_index, embedded, strict=True):
            vectors[index] = self._coerce_vector(vector)
        return vectors

    async def embed_document(self, db: AsyncSession, text: str) -> list[float] | None:
        client = await self.get_client(db)
        vectors = await self.embed_documents_with_client(client, [text])
        return vectors[0] if vectors else None


embedding_service = EmbeddingService()

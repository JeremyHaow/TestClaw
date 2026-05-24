from __future__ import annotations

import logging
import hashlib
import math
import re
from collections.abc import Sequence

from langchain_core.embeddings import Embeddings
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm_gateway import llm_gateway
from app.core.redaction import redact_sensitive_text

logger = logging.getLogger(__name__)

_MAX_EMBEDDING_INPUT_CHARS = 6000
_LOCAL_HASHING_DIMENSIONS = 384


class EmbeddingUnavailableError(RuntimeError):
    """Raised when no configured embedding provider can produce vectors."""


class LocalHashingEmbeddings(Embeddings):
    """Deterministic local vector fallback used when external embeddings are unavailable."""

    dimensions: int = _LOCAL_HASHING_DIMENSIONS

    def _tokens(self, text: str) -> list[str]:
        normalized = text.lower()
        word_tokens = re.findall(r"[a-z0-9_]{2,}", normalized)
        cjk_chars = re.findall(r"[\u4e00-\u9fff]", normalized)
        cjk_bigrams = [
            f"{left}{right}"
            for left, right in zip(cjk_chars, cjk_chars[1:], strict=False)
        ]
        tokens = word_tokens + cjk_chars + cjk_bigrams
        return tokens or [normalized[:96] or "empty"]

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in self._tokens(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            value = int.from_bytes(digest, "big")
            index = value % self.dimensions
            sign = 1.0 if (value >> 9) & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(item * item for item in vector))
        if not norm:
            return vector
        return [item / norm for item in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed_documents(texts)

    async def aembed_query(self, text: str) -> list[float]:
        return self.embed_query(text)


class EmbeddingService:
    def __init__(self) -> None:
        self._local_client = LocalHashingEmbeddings()

    def get_local_client(self) -> Embeddings:
        return self._local_client

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
            logger.warning("Embedding provider unavailable; using local vector fallback: %s", exc)
            return self.get_local_client()

    async def embed_query_with_client(self, client: Embeddings, text: str) -> list[float]:
        prepared = self.prepare_text(text)
        if not prepared:
            raise EmbeddingUnavailableError("No query text was available for embedding")
        try:
            raw_vector = await client.aembed_query(prepared)
        except Exception as exc:
            if isinstance(client, LocalHashingEmbeddings):
                raise EmbeddingUnavailableError(str(exc)) from exc
            logger.warning("Embedding query failed; using local vector fallback: %s", exc)
            raw_vector = await self.get_local_client().aembed_query(prepared)
        vector = self._coerce_vector(raw_vector)
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

        prepared_texts = [prepared for _, prepared in prepared_by_index]
        try:
            embedded = await client.aembed_documents(prepared_texts)
        except Exception as exc:
            if isinstance(client, LocalHashingEmbeddings):
                raise EmbeddingUnavailableError(str(exc)) from exc
            logger.warning("Embedding documents failed; using local vector fallback: %s", exc)
            embedded = await self.get_local_client().aembed_documents(prepared_texts)
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

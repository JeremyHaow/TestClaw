import logging
import math
import re
from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.agent.progress import persist_progress
from app.agent.state import AgentState
from app.agent.tool_registry import install_tool_context, record_tool_call
from app.core.redaction import redact_sensitive_text
from app.models.knowledge import KnowledgeEntry
from app.services.embedding_service import embedding_service

logger = logging.getLogger(__name__)

_MAX_ENTRIES_TO_SCORE = 80
_MAX_SOURCES = 4
_MAX_QUERY_CHARS = 220
_MAX_SNIPPET_CHARS = 360
_MAX_CONTEXT_CHARS = 1600
_MIN_VECTOR_SCORE = 0.2
_STOPWORDS = {
    "http",
    "https",
    "www",
    "com",
    "test",
    "tests",
    "testing",
    "api",
    "ui",
    "run",
    "page",
    "url",
    "the",
    "and",
    "for",
    "with",
    "this",
    "that",
}


def _safe_text(value: Any, limit: int) -> str:
    text = redact_sensitive_text(str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _query_text(state: AgentState) -> str:
    parts = [
        state.get("objective"),
        state.get("target_url"),
        state.get("source_input"),
        state.get("input_type"),
        state.get("test_type"),
    ]
    endpoints = state.get("parsed_api_schema") or []
    for endpoint in endpoints[:8]:
        if isinstance(endpoint, dict):
            parts.extend([endpoint.get("method"), endpoint.get("path"), endpoint.get("summary")])
    return _safe_text(" ".join(str(part) for part in parts if part), _MAX_QUERY_CHARS)


def _tokens(text: str) -> set[str]:
    raw_tokens = re.findall(r"[\w\u4e00-\u9fff]{2,}", text.lower())
    return {token for token in raw_tokens if token not in _STOPWORDS and len(token) <= 48}


def _entry_embedding(entry: KnowledgeEntry) -> list[float] | None:
    value = getattr(entry, "embedding", None)
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


def _score_entry_lexical(entry: KnowledgeEntry, query_tokens: set[str]) -> int:
    if not query_tokens:
        return 0
    content_tokens = _tokens(getattr(entry, "content", "") or "")
    return len(query_tokens & content_tokens)


def _created_at(entry: KnowledgeEntry) -> datetime:
    value = getattr(entry, "created_at", None)
    return value if isinstance(value, datetime) else datetime.min


def _source_payload(entry: KnowledgeEntry, score: float | int, mode: str) -> dict[str, Any]:
    payload_score: float | int
    if mode == "vector":
        payload_score = round(float(score), 4)
    else:
        payload_score = int(score)
    return {
        "id": entry.id,
        "source_script_id": entry.source_script_id,
        "score": payload_score,
        "mode": mode,
        "snippet": _safe_text(entry.content, _MAX_SNIPPET_CHARS),
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


def _context_from_sources(sources: list[dict[str, Any]]) -> str:
    if not sources:
        return ""
    lines: list[str] = []
    for index, source in enumerate(sources, start=1):
        label = source.get("source_script_id") or source.get("id")
        lines.append(f"[{index}] source={label}: {source.get('snippet', '')}")
    return "\n".join(lines)[:_MAX_CONTEXT_CHARS]


def _lexical_sources(
    entries: list[KnowledgeEntry],
    query_tokens: set[str],
) -> tuple[list[dict[str, Any]], int]:
    scored = [
        (score, entry)
        for entry in entries
        if (score := _score_entry_lexical(entry, query_tokens)) > 0
    ]
    scored.sort(key=lambda item: (item[0], _created_at(item[1])), reverse=True)
    sources = [
        _source_payload(entry, score, mode="lexical_fallback")
        for score, entry in scored[:_MAX_SOURCES]
    ]
    return sources, len(scored)


async def _backfill_missing_embeddings(
    db: Any,
    entries: list[KnowledgeEntry],
    client: Any,
) -> tuple[int, str | None]:
    missing = [entry for entry in entries if _entry_embedding(entry) is None]
    if not missing:
        return 0, None
    try:
        vectors = await embedding_service.embed_documents_with_client(
            client,
            [getattr(entry, "content", "") or "" for entry in missing],
        )
    except Exception as exc:
        return 0, _safe_text(exc, 180)

    stored = 0
    for entry, vector in zip(missing, vectors, strict=True):
        if vector:
            entry.embedding = vector
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


def _vector_sources(
    entries: list[KnowledgeEntry],
    query_vector: list[float],
) -> tuple[list[dict[str, Any]], int, int]:
    scored: list[tuple[float, KnowledgeEntry]] = []
    vector_source_count = 0
    for entry in entries:
        vector = _entry_embedding(entry)
        if vector is None:
            continue
        similarity = _cosine_similarity(query_vector, vector)
        if similarity is None:
            continue
        vector_source_count += 1
        if similarity >= _MIN_VECTOR_SCORE:
            scored.append((similarity, entry))

    scored.sort(key=lambda item: (item[0], _created_at(item[1])), reverse=True)
    sources = [
        _source_payload(entry, score, mode="vector")
        for score, entry in scored[:_MAX_SOURCES]
    ]
    return sources, len(scored), vector_source_count


def _fallback_retrieval(
    *,
    query: str,
    entries: list[KnowledgeEntry],
    query_tokens: set[str],
    reason: str,
) -> tuple[dict[str, Any], str, str]:
    sources, match_count = _lexical_sources(entries, query_tokens)
    if sources:
        retrieval = {
            "status": "fallback_lexical",
            "mode": "lexical_fallback",
            "query": query,
            "match_count": match_count,
            "sources": sources,
            "vector_source_count": 0,
            "fallback_reason": reason,
            "effect": (
                "Vector RAG was unavailable, so planner and case generation received an "
                "explicit lexical fallback context."
            ),
        }
        return retrieval, "success", "Vector RAG unavailable; used lexical fallback knowledge"

    retrieval = {
        "status": "unavailable",
        "mode": "unavailable",
        "query": query,
        "match_count": 0,
        "sources": [],
        "vector_source_count": 0,
        "fallback_reason": reason,
        "effect": "Vector RAG retrieval was unavailable; planning continued without prior knowledge.",
    }
    return retrieval, "skipped", "Vector RAG unavailable; no fallback knowledge matched"


async def run(state: AgentState) -> AgentState:
    db = state.get("db_session")
    query = _query_text(state)
    query_tokens = _tokens(query)

    retrieval: dict[str, Any] = {
        "status": "skipped",
        "mode": "skipped",
        "query": query,
        "match_count": 0,
        "sources": [],
        "vector_source_count": 0,
        "fallback_reason": "No database session was available.",
        "effect": "No database session was available, so planner memory was not retrieved.",
    }

    if not db:
        state["rag_retrieval"] = retrieval
        install_tool_context(state)
        record_tool_call(
            state,
            tool_name="memory.retrieve_rag_context",
            layer="memory",
            status="skipped",
            input_summary={"query": query},
            output_summary={"mode": "skipped", "match_count": 0, "vector_source_count": 0},
        )
        await persist_progress(state, "knowledge_retriever", "skipped", "RAG retrieval skipped: no database session")
        return state

    try:
        result = await db.execute(
            select(KnowledgeEntry)
            .order_by(KnowledgeEntry.created_at.desc())
            .limit(_MAX_ENTRIES_TO_SCORE)
        )
        entries = list(result.scalars())

        try:
            client = await embedding_service.get_client(db)
            query_vector = await embedding_service.embed_query_with_client(client, query)
            backfill_count, backfill_failure = await _backfill_missing_embeddings(
                db,
                entries,
                client,
            )
        except Exception as exc:
            retrieval, tool_status, detail = _fallback_retrieval(
                query=query,
                entries=entries,
                query_tokens=query_tokens,
                reason=_safe_text(exc, 180),
            )
        else:
            sources, match_count, vector_source_count = _vector_sources(entries, query_vector)
            if not vector_source_count and entries:
                retrieval, tool_status, detail = _fallback_retrieval(
                    query=query,
                    entries=entries,
                    query_tokens=query_tokens,
                    reason=backfill_failure or "Stored knowledge entries do not have usable embeddings.",
                )
            else:
                context = _context_from_sources(sources)
                if context:
                    state["rag_context"] = context
                    retrieval = {
                        "status": "matched",
                        "mode": "vector",
                        "query": query,
                        "match_count": match_count,
                        "sources": sources,
                        "vector_source_count": vector_source_count,
                        "embedding_backfill_count": backfill_count,
                        "fallback_reason": None,
                        "effect": (
                            "Vector RAG selected similar prior testing notes and injected "
                            "them into planner and case-generation prompts."
                        ),
                    }
                    tool_status = "success"
                    detail = (
                        f"Vector RAG retrieved {len(sources)} source(s) from "
                        f"{vector_source_count} embedded knowledge entries"
                    )
                else:
                    retrieval = {
                        "status": "empty",
                        "mode": "vector",
                        "query": query,
                        "match_count": 0,
                        "sources": [],
                        "vector_source_count": vector_source_count,
                        "embedding_backfill_count": backfill_count,
                        "fallback_reason": backfill_failure,
                        "effect": (
                            "Vector RAG ran successfully but found no similar prior knowledge, "
                            "so planning used live input only."
                        ),
                    }
                    tool_status = "success"
                    detail = "Vector RAG found no similar prior knowledge"

        context = _context_from_sources(retrieval.get("sources") or [])
        if retrieval.get("mode") == "lexical_fallback" and context:
            state["rag_context"] = context

        state["rag_retrieval"] = retrieval
        install_tool_context(state)
        record_tool_call(
            state,
            tool_name="memory.retrieve_rag_context",
            layer="memory",
            status=tool_status,
            input_summary={"query": query, "candidate_count": len(entries)},
            output_summary={
                "mode": retrieval["mode"],
                "match_count": retrieval["match_count"],
                "source_count": len(retrieval["sources"]),
                "vector_source_count": retrieval.get("vector_source_count", 0),
                "fallback_reason": retrieval.get("fallback_reason"),
            },
        )
        state.setdefault("workflow_steps", []).append(
            {"node": "knowledge_retriever", "status": "done", "detail": detail}
        )
        await persist_progress(state, "knowledge_retriever", "done", detail)
    except Exception as exc:
        logger.warning("Knowledge retrieval failed: %s", exc)
        retrieval = {
            "status": "error",
            "mode": "error",
            "query": query,
            "match_count": 0,
            "sources": [],
            "vector_source_count": 0,
            "fallback_reason": _safe_text(exc, 180),
            "effect": "RAG retrieval failed, so planning continued without prior knowledge.",
            "error": _safe_text(exc, 180),
        }
        state["rag_retrieval"] = retrieval
        install_tool_context(state)
        record_tool_call(
            state,
            tool_name="memory.retrieve_rag_context",
            layer="memory",
            status="failed",
            input_summary={"query": query},
            output_summary={"mode": "error", "error": retrieval["error"]},
        )
        state.setdefault("workflow_steps", []).append(
            {"node": "knowledge_retriever", "status": "failed", "detail": retrieval["effect"]}
        )
        await persist_progress(state, "knowledge_retriever", "failed", retrieval["effect"])

    return state

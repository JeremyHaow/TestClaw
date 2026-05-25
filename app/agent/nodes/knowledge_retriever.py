import logging
import re
from datetime import datetime
from typing import Any

from app.agent.progress import persist_progress
from app.agent.state import AgentState
from app.agent.tool_registry import install_tool_context, record_tool_call
from app.core.redaction import redact_sensitive_text
from app.services.embedding_service import embedding_service
from app.services.vector_store import KnowledgeVectorRecord, get_knowledge_vector_store

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
    mission_plan = state.get("agent_mission_plan") or {}
    if isinstance(mission_plan, dict):
        for item in mission_plan.get("memory_needs") or []:
            if isinstance(item, dict):
                parts.append(item.get("query"))
        for subgoal in mission_plan.get("subgoals") or []:
            if isinstance(subgoal, dict):
                parts.append(subgoal.get("title"))
    endpoints = state.get("parsed_api_schema") or []
    for endpoint in endpoints[:8]:
        if isinstance(endpoint, dict):
            parts.extend([endpoint.get("method"), endpoint.get("path"), endpoint.get("summary")])
    return _safe_text(" ".join(str(part) for part in parts if part), _MAX_QUERY_CHARS)


def _tokens(text: str) -> set[str]:
    raw_tokens = re.findall(r"[\w\u4e00-\u9fff]{2,}", text.lower())
    return {token for token in raw_tokens if token not in _STOPWORDS and len(token) <= 48}


def _score_entry_lexical(entry: KnowledgeVectorRecord, query_tokens: set[str]) -> int:
    if not query_tokens:
        return 0
    content_tokens = _tokens(getattr(entry, "content", "") or "")
    return len(query_tokens & content_tokens)


def _created_at(entry: KnowledgeVectorRecord) -> datetime:
    value = getattr(entry, "created_at", None)
    return value if isinstance(value, datetime) else datetime.min


def _source_payload(entry: KnowledgeVectorRecord, score: float | int, mode: str) -> dict[str, Any]:
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
    entries: list[KnowledgeVectorRecord],
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


def _fallback_retrieval(
    *,
    query: str,
    entries: list[KnowledgeVectorRecord],
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
        vector_store = get_knowledge_vector_store()
        entries = await vector_store.load_recent_entries(db, _MAX_ENTRIES_TO_SCORE)

        try:
            client = await embedding_service.get_client(db)
            query_vector = await embedding_service.embed_query_with_client(client, query)
        except Exception as exc:
            retrieval, tool_status, detail = _fallback_retrieval(
                query=query,
                entries=entries,
                query_tokens=query_tokens,
                reason=_safe_text(exc, 180),
            )
        else:
            vector_result = await vector_store.similarity_search(
                db=db,
                entries=entries,
                query=query,
                query_vector=query_vector,
                embedding_client=client,
                embedding_service=embedding_service,
                max_sources=_MAX_SOURCES,
                min_score=_MIN_VECTOR_SCORE,
            )
            retrieval = vector_result.to_dict()
            if not retrieval.get("vector_source_count") and entries:
                retrieval, tool_status, detail = _fallback_retrieval(
                    query=query,
                    entries=entries,
                    query_tokens=query_tokens,
                    reason=retrieval.get("fallback_reason")
                    or "Stored knowledge entries do not have usable embeddings.",
                )
                retrieval["backend"] = vector_result.backend
                retrieval["requested_backend"] = vector_result.requested_backend or vector_result.backend
                retrieval["backend_config"] = vector_result.backend_config or {}
            else:
                context = _context_from_sources(retrieval.get("sources") or [])
                if context:
                    state["rag_context"] = context
                    tool_status = "success"
                    detail = (
                        f"Vector RAG retrieved {len(retrieval.get('sources') or [])} source(s) from "
                        f"{retrieval.get('vector_source_count', 0)} embedded knowledge entries"
                    )
                else:
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
                "backend": retrieval.get("backend"),
                "requested_backend": retrieval.get("requested_backend"),
                "match_count": retrieval["match_count"],
                "source_count": len(retrieval["sources"]),
                "vector_source_count": retrieval.get("vector_source_count", 0),
                "fallback_reason": retrieval.get("fallback_reason"),
            },
            metadata={
                "reason": "Use mission memory needs to retrieve bounded context before planning.",
                "next_decision": "inject_memory_into_planner",
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

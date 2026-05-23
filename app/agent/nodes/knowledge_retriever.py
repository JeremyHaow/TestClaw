import logging
import re
from typing import Any

from sqlalchemy import select

from app.agent.progress import persist_progress
from app.agent.state import AgentState
from app.agent.tool_registry import install_tool_context, record_tool_call
from app.core.redaction import redact_sensitive_text
from app.models.knowledge import KnowledgeEntry

logger = logging.getLogger(__name__)

_MAX_ENTRIES_TO_SCORE = 80
_MAX_SOURCES = 4
_MAX_QUERY_CHARS = 220
_MAX_SNIPPET_CHARS = 360
_MAX_CONTEXT_CHARS = 1600
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


def _score_entry(entry: KnowledgeEntry, query_tokens: set[str]) -> int:
    if not query_tokens:
        return 0
    content_tokens = _tokens(entry.content or "")
    return len(query_tokens & content_tokens)


def _source_payload(entry: KnowledgeEntry, score: int) -> dict[str, Any]:
    return {
        "id": entry.id,
        "source_script_id": entry.source_script_id,
        "score": score,
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


async def run(state: AgentState) -> AgentState:
    db = state.get("db_session")
    query = _query_text(state)
    query_tokens = _tokens(query)

    retrieval: dict[str, Any] = {
        "status": "skipped",
        "query": query,
        "match_count": 0,
        "sources": [],
        "effect": "No database session was available, so planner memory was not retrieved.",
    }

    if not db:
        state["rag_retrieval"] = retrieval
        record_tool_call(
            state,
            tool_name="memory.retrieve_rag_context",
            layer="memory",
            status="skipped",
            input_summary={"query": query},
            output_summary={"match_count": 0},
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
        scored = [
            (score, entry)
            for entry in entries
            if (score := _score_entry(entry, query_tokens)) > 0
        ]
        scored.sort(key=lambda item: (item[0], item[1].created_at), reverse=True)
        sources = [_source_payload(entry, score) for score, entry in scored[:_MAX_SOURCES]]
        context = _context_from_sources(sources)
        if context:
            state["rag_context"] = context
            retrieval = {
                "status": "matched",
                "query": query,
                "match_count": len(scored),
                "sources": sources,
                "effect": "Planner and case generation received these prior testing notes as RAG context.",
            }
            status = "success"
            detail = f"RAG retrieved {len(sources)} relevant knowledge source(s)"
        else:
            retrieval = {
                "status": "empty",
                "query": query,
                "match_count": 0,
                "sources": [],
                "effect": "No relevant prior knowledge matched this target, so planning used live input only.",
            }
            status = "success"
            detail = "RAG retrieval found no relevant prior knowledge"

        state["rag_retrieval"] = retrieval
        install_tool_context(state)
        record_tool_call(
            state,
            tool_name="memory.retrieve_rag_context",
            layer="memory",
            status=status,
            input_summary={"query": query, "candidate_count": len(entries)},
            output_summary={"match_count": retrieval["match_count"], "source_count": len(sources)},
        )
        state.setdefault("workflow_steps", []).append(
            {"node": "knowledge_retriever", "status": "done", "detail": detail}
        )
        await persist_progress(state, "knowledge_retriever", "done", detail)
    except Exception as exc:
        logger.warning("Knowledge retrieval failed: %s", exc)
        retrieval = {
            "status": "error",
            "query": query,
            "match_count": 0,
            "sources": [],
            "effect": "RAG retrieval failed, so planning continued without prior knowledge.",
            "error": _safe_text(exc, 180),
        }
        state["rag_retrieval"] = retrieval
        record_tool_call(
            state,
            tool_name="memory.retrieve_rag_context",
            layer="memory",
            status="failed",
            input_summary={"query": query},
            output_summary={"error": retrieval["error"]},
        )
        state.setdefault("workflow_steps", []).append(
            {"node": "knowledge_retriever", "status": "failed", "detail": retrieval["effect"]}
        )
        await persist_progress(state, "knowledge_retriever", "failed", retrieval["effect"])

    return state

import json
import logging
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from app.agent.progress import persist_progress
from app.agent.state import AgentState
from app.agent.tool_registry import install_tool_context, record_tool_call
from app.core.redaction import redact_sensitive_data, redact_sensitive_text
from app.services.embedding_service import embedding_service
from app.services.vector_store import KnowledgeVectorRecord, get_knowledge_vector_store

logger = logging.getLogger(__name__)

_MAX_ENTRIES_TO_SCORE = 80
_MAX_SOURCES = 4
_MAX_FACTS = 4
_MAX_QUERY_CHARS = 220
_MAX_SNIPPET_CHARS = 360
_MAX_CONTEXT_CHARS = 1600
_MIN_VECTOR_SCORE = 0.2
_MEMORY_CANDIDATE_SCHEMA = "testclaw.memory_candidate.v1"
_MEMORY_CANDIDATE_MARKER = "TESTCLAW_MEMORY_CANDIDATE_V1"
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


def _parse_memory_candidate_content(content: Any) -> dict[str, Any] | None:
    text = str(content or "").strip()
    if not text:
        return None
    if _MEMORY_CANDIDATE_MARKER in text:
        text = text.split(_MEMORY_CANDIDATE_MARKER, 1)[1].strip()
    start = text.find("{")
    if start < 0:
        return None
    try:
        parsed = json.loads(text[start:])
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    if parsed.get("schema_version") != _MEMORY_CANDIDATE_SCHEMA:
        return None
    return redact_sensitive_data(parsed)


def _host(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
    except Exception:
        return ""
    return parsed.netloc.lower()


def _memory_is_target_related(
    candidate: dict[str, Any],
    state: AgentState,
    query_tokens: set[str],
) -> bool:
    candidate_target = str(candidate.get("target_hint") or "")
    current_target = str(state.get("target_url") or state.get("source_input") or "")
    candidate_host = _host(candidate_target)
    current_host = _host(current_target)
    if candidate_host and current_host:
        return candidate_host == current_host
    if candidate_target and current_target:
        if candidate_target in current_target or current_target in candidate_target:
            return True
    candidate_tokens = _tokens(
        " ".join(
            str(item or "")
            for item in [
                candidate.get("target_hint"),
                candidate.get("objective"),
                candidate.get("failure_type"),
                candidate.get("planner_hint"),
            ]
        )
    )
    return len(query_tokens & candidate_tokens) >= 2


def _compact_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return redact_sensitive_data(
        {
            "schema_version": candidate.get("schema_version"),
            "kind": candidate.get("kind"),
            "confidence": candidate.get("confidence"),
            "source_run_id": candidate.get("source_run_id"),
            "target_hint": candidate.get("target_hint"),
            "stage": candidate.get("stage"),
            "failure_type": candidate.get("failure_type"),
            "next_action": candidate.get("next_action"),
            "final_verdict": candidate.get("final_verdict"),
        }
    )


def _fact_from_candidate(
    candidate: dict[str, Any],
    source: dict[str, Any],
    state: AgentState,
    query_tokens: set[str],
) -> dict[str, Any] | None:
    if str(candidate.get("confidence") or "").lower() != "high":
        return None
    if not _memory_is_target_related(candidate, state, query_tokens):
        return None
    raw_fact = None
    for item in candidate.get("facts") or []:
        if isinstance(item, dict):
            raw_fact = item
            break
    if raw_fact is None:
        raw_fact = {}
    fact = {
        "fact_type": raw_fact.get("fact_type") or candidate.get("kind") or "execution_memory",
        "confidence": "high",
        "source_id": source.get("id"),
        "source_script_id": source.get("source_script_id") or candidate.get("source_run_id"),
        "target_hint": candidate.get("target_hint"),
        "stage": candidate.get("stage"),
        "failure_type": raw_fact.get("failure_type") or candidate.get("failure_type"),
        "next_action": raw_fact.get("next_action") or candidate.get("next_action"),
        "summary": _safe_text(raw_fact.get("summary") or candidate.get("reason"), 360),
        "planner_hint": _safe_text(raw_fact.get("planner_hint") or candidate.get("planner_hint"), 360),
        "observation_refs": candidate.get("observation_refs") or [],
        "created_at": source.get("created_at"),
    }
    return redact_sensitive_data(fact)


def _enrich_sources_with_memory(
    sources: list[dict[str, Any]],
    entries: list[KnowledgeVectorRecord],
    state: AgentState,
    query_tokens: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entries_by_id = {str(entry.id): entry for entry in entries}
    enriched: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    seen_facts: set[str] = set()

    for source in sources:
        payload = dict(source)
        entry = entries_by_id.get(str(source.get("id")))
        candidate = _parse_memory_candidate_content(entry.content if entry else source.get("snippet"))
        if candidate:
            payload["memory_candidate"] = _compact_candidate(candidate)
            fact = _fact_from_candidate(candidate, payload, state, query_tokens)
            if fact:
                marker = json.dumps(fact, ensure_ascii=False, sort_keys=True, default=str)
                if marker not in seen_facts:
                    seen_facts.add(marker)
                    facts.append(fact)
        enriched.append(payload)

    return enriched, facts[:_MAX_FACTS]


def _context_from_sources(sources: list[dict[str, Any]], facts: list[dict[str, Any]] | None = None) -> str:
    if not sources:
        return ""
    lines: list[str] = []
    if facts:
        lines.append("Structured memory facts (high confidence, target related):")
        for index, fact in enumerate(facts, start=1):
            label = fact.get("source_script_id") or fact.get("source_id")
            detail = fact.get("planner_hint") or fact.get("summary") or ""
            failure = f" failure_type={fact.get('failure_type')}" if fact.get("failure_type") else ""
            lines.append(
                f"- fact[{index}] source={label} type={fact.get('fact_type')}{failure}: {_safe_text(detail, 260)}"
            )
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
        "facts": [],
        "fact_count": 0,
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
            output_summary={"mode": "skipped", "match_count": 0, "vector_source_count": 0, "fact_count": 0},
        )
        state["rag_facts"] = []
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
                source_count = len(retrieval.get("sources") or [])
                if source_count:
                    tool_status = "success"
                    detail = (
                        f"Vector RAG retrieved {source_count} source(s) from "
                        f"{retrieval.get('vector_source_count', 0)} embedded knowledge entries"
                    )
                else:
                    tool_status = "success"
                    detail = "Vector RAG found no similar prior knowledge"

        enriched_sources, facts = _enrich_sources_with_memory(
            retrieval.get("sources") or [],
            entries,
            state,
            query_tokens,
        )
        retrieval["sources"] = enriched_sources
        retrieval["facts"] = facts
        retrieval["fact_count"] = len(facts)

        context = _context_from_sources(enriched_sources, facts)
        if context:
            state["rag_context"] = context

        state["rag_retrieval"] = retrieval
        state["rag_facts"] = facts
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
                "fact_count": retrieval.get("fact_count", 0),
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
            "facts": [],
            "fact_count": 0,
            "vector_source_count": 0,
            "fallback_reason": _safe_text(exc, 180),
            "effect": "RAG retrieval failed, so planning continued without prior knowledge.",
            "error": _safe_text(exc, 180),
        }
        state["rag_retrieval"] = retrieval
        state["rag_facts"] = []
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

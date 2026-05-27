import json
import logging
import re
from typing import Any
from urllib.parse import urlsplit

from langchain_core.messages import HumanMessage

from app.agent.prompts import RCA_PROMPT
from app.agent.state import AgentState
from app.core.llm_gateway import ainvoke_with_timeout, llm_gateway
from app.core.redaction import redact_sensitive_data, redact_sensitive_text
from app.services.knowledge_service import knowledge_service

logger = logging.getLogger(__name__)

MEMORY_CANDIDATE_SCHEMA = "testclaw.memory_candidate.v1"
MEMORY_CANDIDATE_MARKER = "TESTCLAW_MEMORY_CANDIDATE_V1"
_MAX_MEMORY_CANDIDATES = 8


def _to_text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        lines = []
        for index, item in enumerate(value, start=1):
            text = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
            lines.append(f"{index}. {text}")
        return "\n".join(lines) or fallback
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _safe_text(value: Any, limit: int = 500) -> str:
    text = redact_sensitive_text(str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _last_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    for item in reversed(_safe_list(value)):
        if isinstance(item, dict):
            return item
    return {}


def _target_hint(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
    except Exception:
        return _safe_text(text, 240)
    if parsed.scheme and parsed.netloc:
        return _safe_text(f"{parsed.scheme}://{parsed.netloc}{parsed.path}", 240)
    return _safe_text(text, 240)


def _latest_evaluation(state: AgentState) -> dict[str, Any]:
    return (
        _last_dict(state.get("agent_protocol_evaluations"))
        or _last_dict(state.get("evidence_evaluation"))
        or _last_dict(state.get("agent_evaluations"))
    )


def _failure_type_from_state(state: AgentState, evaluation: dict[str, Any]) -> str | None:
    failure_type = evaluation.get("failure_type")
    if failure_type:
        return _safe_text(failure_type, 120)
    protocol = state.get("agent_protocol_summary") if isinstance(state.get("agent_protocol_summary"), dict) else {}
    by_failure = protocol.get("by_failure_type") if isinstance(protocol, dict) else {}
    if isinstance(by_failure, dict) and by_failure:
        return str(max(by_failure.items(), key=lambda item: int(item[1] or 0))[0])
    for observation in reversed(_safe_list(state.get("agent_observations"))):
        if isinstance(observation, dict) and observation.get("failure_type"):
            return _safe_text(observation.get("failure_type"), 120)
    return None


def _related_observations(state: AgentState, evaluation: dict[str, Any]) -> list[dict[str, Any]]:
    observations = [item for item in _safe_list(state.get("agent_observations")) if isinstance(item, dict)]
    if not observations:
        return []
    wanted_ids = {str(item) for item in _safe_list(evaluation.get("observation_ids")) if item}
    if wanted_ids:
        selected = [item for item in observations if str(item.get("observation_id")) in wanted_ids]
        if selected:
            return selected[-6:]
    failure_type = _failure_type_from_state(state, evaluation)
    if failure_type:
        selected = [item for item in observations if item.get("failure_type") == failure_type]
        if selected:
            return selected[-6:]
    return observations[-4:]


def _observation_refs(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for item in observations:
        refs.append(
            {
                "observation_id": item.get("observation_id"),
                "stage": item.get("stage"),
                "layer": item.get("layer"),
                "tool_name": item.get("tool_name"),
                "status": item.get("status"),
                "outcome": item.get("outcome"),
                "failure_type": item.get("failure_type"),
                "summary": _safe_text(item.get("summary"), 240),
                "evidence_ids": [str(evidence_id) for evidence_id in _safe_list(item.get("evidence_ids"))[:6]],
            }
        )
    return redact_sensitive_data(refs)


def _evidence_refs_from_observations(observations: list[dict[str, Any]]) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    for observation in observations:
        for evidence_id in _safe_list(observation.get("evidence_ids")):
            key = str(evidence_id)
            if key and key not in seen:
                seen.add(key)
                refs.append(key)
    return refs[:12]


def _memory_confidence(
    evaluation: dict[str, Any],
    *,
    observations: list[dict[str, Any]],
    final_report: dict[str, Any],
    has_failure: bool,
) -> str:
    confidence = str(evaluation.get("confidence") or "").lower()
    if confidence in {"high", "medium", "low"}:
        return confidence
    verdict = str(final_report.get("overall_verdict") or "").upper()
    if evaluation.get("sufficient_evidence") and (observations or verdict == "PASS"):
        return "high"
    if has_failure or _failure_type_from_state({}, evaluation):
        return "medium"
    return "low"


def _memory_kind(
    evaluation: dict[str, Any],
    *,
    final_report: dict[str, Any],
    failure_type: str | None,
    has_failure: bool,
    human_question: str | None,
) -> str:
    next_action = str(evaluation.get("next_action") or "").lower()
    verdict = str(final_report.get("overall_verdict") or "").upper()
    if human_question or next_action == "ask_human":
        return "known_blocker"
    if failure_type or has_failure or next_action in {"replan_api", "replan_ui", "retry_same_action"}:
        return "failure_recovery"
    if evaluation.get("sufficient_evidence") or verdict == "PASS":
        return "successful_strategy"
    return "execution_note"


def _planner_hint(
    state: AgentState,
    evaluation: dict[str, Any],
    bug_report: dict[str, Any] | None,
    final_report: dict[str, Any],
) -> str:
    recommendations = _safe_list(final_report.get("recommendations"))
    return _safe_text(
        evaluation.get("replan_hint")
        or evaluation.get("replan_instructions")
        or state.get("agent_retry_feedback")
        or state.get("agent_replan_feedback")
        or (bug_report or {}).get("fix_suggestion")
        or (recommendations[0] if recommendations else "")
        or evaluation.get("reason")
        or final_report.get("summary")
        or state.get("last_error"),
        420,
    )


def _memory_fact(
    *,
    kind: str,
    failure_type: str | None,
    summary: str,
    planner_hint: str,
    next_action: str,
) -> dict[str, Any]:
    if kind == "successful_strategy":
        fact_type = "successful_strategy"
    elif kind == "known_blocker":
        fact_type = "known_blocker"
    else:
        fact_type = "failure_recovery" if failure_type else "execution_note"
    return {
        "fact_type": fact_type,
        "summary": summary,
        "failure_type": failure_type,
        "next_action": next_action,
        "planner_hint": planner_hint,
    }


def _build_memory_candidate(
    state: AgentState,
    bug_report: dict[str, Any] | None,
) -> dict[str, Any] | None:
    evaluation = _latest_evaluation(state)
    final_report = state.get("final_report") if isinstance(state.get("final_report"), dict) else {}
    has_failure = bool(state.get("last_error"))
    if not evaluation and not has_failure:
        return None

    observations = _related_observations(state, evaluation)
    failure_type = _failure_type_from_state(state, evaluation)
    human_question = _safe_text(
        evaluation.get("human_question") or state.get("agent_human_question"),
        300,
    )
    confidence = _memory_confidence(
        evaluation,
        observations=observations,
        final_report=final_report,
        has_failure=has_failure,
    )
    kind = _memory_kind(
        evaluation,
        final_report=final_report,
        failure_type=failure_type,
        has_failure=has_failure,
        human_question=human_question,
    )
    next_action = _safe_text(evaluation.get("next_action") or "report", 80)
    reason = _safe_text(
        evaluation.get("reason")
        or (bug_report or {}).get("root_cause")
        or final_report.get("summary")
        or state.get("last_error")
        or "Execution memory generated from run evidence.",
        420,
    )
    hint = _planner_hint(state, evaluation, bug_report, final_report)
    fact = _memory_fact(
        kind=kind,
        failure_type=failure_type,
        summary=reason,
        planner_hint=hint,
        next_action=next_action,
    )
    candidate = {
        "schema_version": MEMORY_CANDIDATE_SCHEMA,
        "kind": kind,
        "confidence": confidence,
        "source": "execution_evaluation",
        "source_run_id": state.get("task_id"),
        "target_hint": _target_hint(state.get("target_url") or state.get("source_input")),
        "objective": _safe_text(state.get("objective"), 260),
        "test_type": state.get("test_type"),
        "input_type": state.get("input_type"),
        "stage": evaluation.get("stage") or state.get("agent_execution_stage"),
        "next_action": next_action,
        "sufficient_evidence": bool(evaluation.get("sufficient_evidence")),
        "failure_type": failure_type,
        "missing_evidence": [_safe_text(item, 180) for item in _safe_list(evaluation.get("missing_evidence"))[:6]],
        "reason": reason,
        "planner_hint": hint,
        "human_question": human_question or None,
        "facts": [fact],
        "evaluation_ref": evaluation.get("evaluation_id"),
        "observation_refs": _observation_refs(observations),
        "evidence_refs": _evidence_refs_from_observations(observations),
        "protocol_summary": state.get("agent_protocol_summary") if isinstance(state.get("agent_protocol_summary"), dict) else {},
        "final_verdict": final_report.get("overall_verdict"),
    }
    return redact_sensitive_data(candidate)


def _append_memory_candidate(state: AgentState, candidate: dict[str, Any]) -> None:
    candidates = state.setdefault("memory_candidates", [])
    candidates.append(candidate)
    if len(candidates) > _MAX_MEMORY_CANDIDATES:
        del candidates[:-_MAX_MEMORY_CANDIDATES]


def _knowledge_content(
    candidate: dict[str, Any] | None,
    bug_report: dict[str, Any] | None,
) -> str:
    if candidate:
        fact = _last_dict(candidate.get("facts"))
        lines = [
            MEMORY_CANDIDATE_MARKER,
            f"Kind: {_safe_text(candidate.get('kind'), 80)}",
            f"Target: {_safe_text(candidate.get('target_hint'), 240)}",
            f"Fact: {_safe_text(fact.get('fact_type'), 80)}",
            f"Summary: {_safe_text(fact.get('summary') or candidate.get('reason'), 420)}",
            f"Planner Hint: {_safe_text(fact.get('planner_hint') or candidate.get('planner_hint'), 420)}",
            json.dumps(candidate, ensure_ascii=False, default=str),
        ]
        return redact_sensitive_text("\n".join(line for line in lines if line.strip()))

    return redact_sensitive_text(
        f"Bug: {_to_text((bug_report or {}).get('title'), 'Automated test failure detected')}\n"
        f"Root Cause: {_to_text((bug_report or {}).get('root_cause'), 'Unknown root cause')}\n"
        f"Fix: {_to_text((bug_report or {}).get('fix_suggestion'), '')}"
    )


async def run(state: AgentState) -> AgentState:
    has_failure = bool(state.get("last_error"))
    if not has_failure and not _latest_evaluation(state):
        state.setdefault("workflow_steps", []).append(
            {"node": "knowledge_sink", "status": "done", "detail": "Task completed successfully; no reusable execution memory to store"}
        )
        return state

    db = state.get("db_session")
    execution_result = state.get("execution_result") or {}
    stderr = execution_result.get("stderr", "")
    bug_report = None

    if has_failure and db:
        try:
            llm = await llm_gateway.get_planner(db)
            prompt = RCA_PROMPT.format(
                stderr=stderr[:3000],
                network_logs="No network logs available",
            )
            resp = await ainvoke_with_timeout(
                llm,
                [HumanMessage(content=prompt)],
                call_name="knowledge_sink.generate_rca",
            )
            content = resp.content if hasattr(resp, "content") else str(resp)
            text = content.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                bug_report = {
                    "title": _to_text(parsed.get("title"), "Automated test failure detected")[:255],
                    "root_cause": _to_text(parsed.get("root_cause"), state["last_error"]),
                    "reproduce_steps": _to_text(
                        parsed.get("reproduce_steps"),
                        "Run the generated script against the target environment.",
                    ),
                    "fix_suggestion": _to_text(
                        parsed.get("fix_suggestion"),
                        "Inspect the execution log and target application behavior.",
                    ),
                }
        except Exception as e:
            logger.warning("Knowledge sink LLM call failed: %s, using fallback", e)

    if has_failure and bug_report is None:
        bug_report = {
            "title": "Automated test failure detected",
            "root_cause": state["last_error"],
            "reproduce_steps": "Run the generated script against the target environment.",
            "fix_suggestion": "Inspect the execution log and target application behavior.",
        }

    if bug_report:
        state["bug_report"] = bug_report

    if db and bug_report:
        try:
            from app.models.bug_report import BugReport as BugReportModel
            from datetime import datetime

            report = BugReportModel(
                task_id=state.get("task_id", ""),
                title=_to_text(bug_report.get("title"), "Automated test failure detected")[:255],
                root_cause=_to_text(bug_report.get("root_cause"), "Unknown root cause"),
                reproduce_steps=_to_text(bug_report.get("reproduce_steps"), "Run the task again."),
                fix_suggestion=_to_text(bug_report.get("fix_suggestion"), ""),
                created_at=datetime.utcnow(),
            )
            db.add(report)
            await db.commit()
        except Exception as e:
            await db.rollback()
            logger.warning("Failed to persist bug report: %s", e)

    memory_candidate = _build_memory_candidate(state, bug_report)
    if memory_candidate:
        _append_memory_candidate(state, memory_candidate)

    state.setdefault("workflow_steps", []).append(
        {
            "node": "knowledge_sink",
            "status": "done",
            "detail": "Bug report and memory candidate generated" if bug_report and memory_candidate else (
                "Bug report generated" if bug_report else "Memory candidate generated"
            ),
        }
    )

    # After persisting bug report, also store reusable execution memory.
    if db and (bug_report or memory_candidate):
        try:
            await knowledge_service.create(
                db,
                content=_knowledge_content(memory_candidate, bug_report),
                source_script_id=state.get("task_id"),
            )
        except Exception as e:
            await db.rollback()
            logger.warning("Failed to persist knowledge: %s", e)

    return state

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage

from app.agent.progress import persist_progress
from app.agent.prompts import EVIDENCE_EVALUATOR_PROMPT
from app.agent.state import AgentState
from app.agent.tool_registry import install_tool_context, record_tool_call
from app.config import settings
from app.core.llm_gateway import llm_gateway
from app.core.redaction import redact_sensitive_data

logger = logging.getLogger(__name__)

_REPORT = "report"
_CONTINUE_TO_UI = "continue_to_ui"
_REPLAN_API = "replan_api"
_REPLAN_UI = "replan_ui"
_ACTION_TO_NODE = {
    _REPORT: "reporter",
    _CONTINUE_TO_UI: "ui_login",
    _REPLAN_API: "tc_generator",
    _REPLAN_UI: "ui_test_planner",
}
_MAX_EVALUATIONS = 12


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _compact_text(value: Any, limit: int = 600) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _parse_json_object(content: str) -> dict[str, Any]:
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    if not text.startswith("{"):
        match = re.search(r"\{.*\}", text, flags=re.S)
        if match:
            text = match.group(0)
    try:
        parsed = json.loads(text)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _stage_from_state(state: AgentState) -> str:
    explicit = str(state.get("agent_execution_stage") or "").lower()
    if explicit in {"api", "ui"}:
        return explicit
    if state.get("ui_execution_result") is not None:
        return "ui"
    return "api"


def _has_ui_target(state: AgentState) -> bool:
    test_type = str(state.get("test_type") or "auto").lower()
    if test_type == "api":
        return False
    input_type = str(state.get("input_type") or "").lower()
    return test_type in {"ui", "full"} or input_type == "url" or bool(
        state.get("ui_seed_url") or state.get("ui_cases")
    )


def _has_api_target(state: AgentState) -> bool:
    test_type = str(state.get("test_type") or "auto").lower()
    if test_type == "ui":
        return False
    return test_type in {"api", "full"} or bool(
        state.get("parsed_api_schema") or state.get("api_cases") or state.get("base_url_override")
    )


def _safe_schema_endpoint_count(state: AgentState) -> int:
    return sum(
        1
        for endpoint in _safe_list(state.get("parsed_api_schema"))
        if isinstance(endpoint, dict)
        and str(endpoint.get("method") or "GET").upper() in {"GET", "HEAD", "OPTIONS"}
    )


def _setup_failed(state: AgentState) -> bool:
    setup_required = bool((state.get("setup_instructions") or state.get("login_instructions") or "").strip())
    setup_result = state.get("setup_result") or state.get("login_result") or {}
    return setup_required and setup_result.get("required") and state.get("login_verified") is False


def _api_summary(state: AgentState) -> dict[str, Any]:
    result = state.get("api_execution_result") or {}
    rows = _safe_list(result.get("results"))
    failure_samples = []
    skip_reasons = []
    for row in rows[:8]:
        if not isinstance(row, dict):
            continue
        if row.get("skip_reason"):
            skip_reasons.append(_compact_text(row.get("skip_reason"), 180))
        if row.get("passed") is False:
            failure_samples.append(
                {
                    "label": row.get("label"),
                    "method": row.get("method"),
                    "url": row.get("url"),
                    "status_code": row.get("status_code"),
                    "failure_type": row.get("failure_type"),
                    "error": _compact_text(row.get("error") or row.get("failure_reason"), 240),
                }
            )
    return {
        "requested": _has_api_target(state),
        "total": _safe_int(result.get("total")),
        "candidate_total": _safe_int(result.get("candidate_total")),
        "executed": _safe_int(result.get("executed")),
        "passed": _safe_int(result.get("passed")),
        "failed": _safe_int(result.get("failed")),
        "skipped": _safe_int(result.get("skipped")),
        "http_executed": _safe_int(result.get("http_executed")),
        "all_passed": bool(result.get("all_passed")),
        "complete": bool(result.get("complete")),
        "request_selection": result.get("request_selection") or state.get("api_request_selection") or {},
        "safe_schema_endpoint_count": _safe_schema_endpoint_count(state),
        "failure_samples": failure_samples,
        "skip_reasons": skip_reasons[:4],
    }


def _ui_summary(state: AgentState) -> dict[str, Any]:
    result = state.get("ui_execution_result") or {}
    commands = [item for item in _safe_list(result.get("commands")) if isinstance(item, dict)]
    failed_commands = [
        {
            "case_index": command.get("case_index"),
            "case_title": command.get("case_title"),
            "command": command.get("normalized_command") or command.get("command"),
            "status_code": command.get("status_code"),
            "stderr": _compact_text(command.get("stderr"), 240),
        }
        for command in commands
        if command.get("status") != "skipped"
        and command.get("passed") is not True
        and _safe_int(command.get("status_code")) != 0
    ][:6]
    snapshot_count = len(_safe_list(result.get("snapshot_texts")))
    screenshot_count = len(_safe_list(result.get("screenshots")))
    return {
        "requested": _has_ui_target(state),
        "total": _safe_int(result.get("total")),
        "completed": _safe_int(result.get("completed")),
        "passed": _safe_int(result.get("passed")),
        "failed": _safe_int(result.get("failed")),
        "command_total": _safe_int(result.get("command_total")),
        "command_completed": _safe_int(result.get("command_completed")),
        "command_failed": _safe_int(result.get("command_failed")),
        "screenshots": screenshot_count,
        "snapshots": snapshot_count,
        "all_passed": bool(result.get("all_passed")),
        "complete": bool(result.get("complete")),
        "skip_reason": result.get("skip_reason"),
        "failed_commands": failed_commands,
        "has_snapshot_context": snapshot_count > 0 or bool(state.get("ui_login_snapshot")),
        "setup_failed": _setup_failed(state),
    }


def _tool_call_summary(state: AgentState) -> list[dict[str, Any]]:
    calls = [
        call for call in _safe_list(state.get("tool_calls"))
        if isinstance(call, dict)
    ][-12:]
    return [
        {
            "tool": call.get("tool"),
            "layer": call.get("layer"),
            "status": call.get("status"),
            "case_index": call.get("case_index"),
            "case_title": call.get("case_title"),
            "input": call.get("input"),
            "output": call.get("output"),
        }
        for call in calls
    ]


def _evidence_summary(state: AgentState, stage: str) -> dict[str, Any]:
    return redact_sensitive_data(
        {
            "stage": stage,
            "test_type": state.get("test_type"),
            "input_type": state.get("input_type"),
            "api": _api_summary(state),
            "ui": _ui_summary(state),
            "plans": {
                "api_plan": bool(state.get("api_plan")),
                "ui_plan": bool(state.get("ui_plan")),
                "api_cases": len(_safe_list(state.get("api_cases"))),
                "ui_cases": len(_safe_list(state.get("ui_cases"))),
            },
            "replan_counts": state.get("agent_replan_counts") or {},
            "last_error": _compact_text(state.get("last_error"), 300),
        }
    )


def _allowed_actions(state: AgentState, stage: str) -> list[str]:
    if stage == "api":
        actions = [_REPORT, _REPLAN_API]
        if _has_ui_target(state):
            actions.insert(1, _CONTINUE_TO_UI)
        return actions
    return [_REPORT, _REPLAN_UI]


def _replan_count(state: AgentState, stage: str) -> int:
    counts = state.get("agent_replan_counts")
    if isinstance(counts, dict):
        return _safe_int(counts.get(stage))
    return 0


def _can_replan(state: AgentState, stage: str) -> bool:
    return _replan_count(state, stage) < max(0, int(settings.AGENT_MAX_REPLAN_ATTEMPTS))


def _api_needs_replan(state: AgentState, summary: dict[str, Any]) -> tuple[bool, str, list[str]]:
    api = summary["api"]
    if not api["requested"]:
        return False, "API stage is not requested for this run.", []
    if api["total"] == 0:
        if state.get("parsed_api_schema") or state.get("api_cases") or state.get("base_url_override"):
            return True, "API stage produced no executable request candidates.", [
                "需要重新生成可执行的 API 请求，优先选择安全的 GET/HEAD/OPTIONS 或明确说明受策略限制。"
            ]
        return False, "No API target is available to replan from.", []
    if api["executed"] == 0 and api["safe_schema_endpoint_count"] > 0:
        return True, "API stage skipped every request even though safe schema endpoints are available.", [
            "使用 schema 中的安全方法重新生成正向可达性检查。"
        ]
    if (
        api["executed"] <= 1
        and api["failed"] >= 1
        and api["safe_schema_endpoint_count"] > 1
        and api["request_selection"].get("source") == "api_cases"
    ):
        return True, "Only one curated API attempt failed while additional safe schema endpoints exist.", [
            "不要停在单个失败用例；从 OpenAPI schema 重新选择多个安全端点收集对照证据。"
        ]
    return False, "API evidence is sufficient for this bounded pass.", []


def _ui_needs_replan(state: AgentState, summary: dict[str, Any]) -> tuple[bool, str, list[str]]:
    ui = summary["ui"]
    if not ui["requested"]:
        return False, "UI stage is not requested for this run.", []
    if ui["setup_failed"]:
        return False, "UI setup failed and requires user intervention before more automation.", []
    if str(state.get("source_input") or "").strip().lower() == "suite":
        return False, "Selected suite cases preserve user-provided execution semantics.", []
    if ui["total"] == 0:
        return True, "UI stage produced no executable UI cases.", [
            "基于当前页面快照重新生成可执行 UI 用例。"
        ]
    selector_failure = any(
        "not found" in str(command.get("stderr") or "").lower()
        or "does not match any elements" in str(command.get("stderr") or "").lower()
        for command in ui["failed_commands"]
    )
    if selector_failure and ui["has_snapshot_context"]:
        return True, "UI commands failed on element lookup while snapshot context is available.", [
            "重新读取快照语义，优先使用当前页面 ref 或可见文本生成下一轮动作。"
        ]
    if ui["command_completed"] <= 1 and not ui["all_passed"]:
        return True, "UI attempt stopped after a single shallow command without enough evidence.", [
            "至少收集页面快照和截图，再根据可见入口继续执行。"
        ]
    if ui["screenshots"] == 0 and not ui["all_passed"]:
        return True, "UI attempt failed without screenshot evidence.", [
            "下一轮必须包含截图证据和失败前后的页面快照。"
        ]
    return False, "UI evidence is sufficient for this bounded pass.", []


def _guardrail_decision(
    state: AgentState,
    stage: str,
    summary: dict[str, Any],
    allowed_actions: list[str],
) -> dict[str, Any]:
    if stage == "api":
        needs_replan, reason, missing = _api_needs_replan(state, summary)
        if needs_replan and _REPLAN_API in allowed_actions and _can_replan(state, "api"):
            return {
                "sufficient_evidence": False,
                "confidence": "medium",
                "next_action": _REPLAN_API,
                "reason": reason,
                "diagnostics": missing,
                "missing_evidence": missing,
                "replan_instructions": "Regenerate API cases from available schema/base URL and favor safe executable probes before reporting.",
                "source": "guardrail",
            }
        if needs_replan and not _can_replan(state, "api"):
            return {
                "sufficient_evidence": False,
                "confidence": "medium",
                "next_action": _REPORT,
                "reason": f"{reason} Replan limit reached.",
                "diagnostics": [*missing, "已达到 API 重规划上限，报告中保留阻塞诊断。"],
                "missing_evidence": missing,
                "replan_instructions": "",
                "source": "guardrail",
            }
        if _CONTINUE_TO_UI in allowed_actions:
            return {
                "sufficient_evidence": True,
                "confidence": "medium",
                "next_action": _CONTINUE_TO_UI,
                "reason": "API stage is complete enough; continuing to requested UI coverage.",
                "diagnostics": [],
                "missing_evidence": [],
                "replan_instructions": "",
                "source": "guardrail",
            }
        return {
            "sufficient_evidence": True,
            "confidence": "medium",
            "next_action": _REPORT,
            "reason": "API stage reached a reportable stopping point.",
            "diagnostics": [],
            "missing_evidence": [],
            "replan_instructions": "",
            "source": "guardrail",
        }

    needs_replan, reason, missing = _ui_needs_replan(state, summary)
    if needs_replan and _REPLAN_UI in allowed_actions and _can_replan(state, "ui"):
        return {
            "sufficient_evidence": False,
            "confidence": "medium",
            "next_action": _REPLAN_UI,
            "reason": reason,
            "diagnostics": missing,
            "missing_evidence": missing,
            "replan_instructions": "Regenerate UI cases from the latest snapshot evidence and avoid repeating failed selectors.",
            "source": "guardrail",
        }
    if needs_replan and not _can_replan(state, "ui"):
        return {
            "sufficient_evidence": False,
            "confidence": "medium",
            "next_action": _REPORT,
            "reason": f"{reason} Replan limit reached.",
            "diagnostics": [*missing, "已达到 UI 重规划上限，报告中保留阻塞诊断。"],
            "missing_evidence": missing,
            "replan_instructions": "",
            "source": "guardrail",
        }
    return {
        "sufficient_evidence": True,
        "confidence": "medium",
        "next_action": _REPORT,
        "reason": "UI stage reached a reportable stopping point.",
        "diagnostics": [],
        "missing_evidence": [],
        "replan_instructions": "",
        "source": "guardrail",
    }


async def _model_decision(
    state: AgentState,
    stage: str,
    summary: dict[str, Any],
    tool_calls: list[dict[str, Any]],
    allowed_actions: list[str],
) -> tuple[dict[str, Any] | None, str | None]:
    db = state.get("db_session")
    if not db:
        return None, "No database session/model provider was available."
    try:
        llm = await llm_gateway.get_planner(db)
        prompt = EVIDENCE_EVALUATOR_PROMPT.format(
            stage=stage,
            test_type=state.get("test_type"),
            objective=state.get("objective", ""),
            target_url=state.get("target_url", ""),
            evidence_summary=json.dumps(summary, ensure_ascii=False, default=str)[:6000],
            tool_call_summary=json.dumps(tool_calls, ensure_ascii=False, default=str)[:4000],
            prior_evaluations=json.dumps(
                _safe_list(state.get("agent_evaluations"))[-4:],
                ensure_ascii=False,
                default=str,
            )[:3000],
            allowed_actions=", ".join(allowed_actions),
        )
        resp = await llm.ainvoke([HumanMessage(content=prompt)])
        content = resp.content if hasattr(resp, "content") else str(resp)
        parsed = _parse_json_object(str(content))
        return (parsed or None), None
    except Exception as exc:
        logger.warning("Execution evidence evaluator LLM call failed: %s", exc)
        return None, _compact_text(exc, 240)


def _normalize_model_decision(
    model_decision: dict[str, Any] | None,
    allowed_actions: list[str],
) -> dict[str, Any] | None:
    if not isinstance(model_decision, dict):
        return None
    action = str(model_decision.get("next_action") or "").strip().lower()
    if action not in allowed_actions:
        return None
    diagnostics = [
        _compact_text(item, 240)
        for item in _safe_list(model_decision.get("diagnostics"))
        if _compact_text(item, 240)
    ]
    missing = [
        _compact_text(item, 240)
        for item in _safe_list(model_decision.get("missing_evidence"))
        if _compact_text(item, 240)
    ]
    return {
        "sufficient_evidence": bool(model_decision.get("sufficient_evidence")),
        "confidence": str(model_decision.get("confidence") or "medium").lower(),
        "next_action": action,
        "reason": _compact_text(model_decision.get("reason"), 500),
        "diagnostics": diagnostics,
        "missing_evidence": missing,
        "replan_instructions": _compact_text(model_decision.get("replan_instructions"), 800),
        "source": "llm",
    }


def _merge_decisions(
    *,
    guardrail: dict[str, Any],
    model_decision: dict[str, Any] | None,
    state: AgentState,
    stage: str,
) -> dict[str, Any]:
    if guardrail["next_action"] in {_REPLAN_API, _REPLAN_UI}:
        decision = dict(guardrail)
        if model_decision:
            decision["source"] = "llm+guardrail"
            if model_decision.get("replan_instructions"):
                decision["replan_instructions"] = model_decision["replan_instructions"]
            if model_decision.get("diagnostics"):
                decision["diagnostics"] = list(dict.fromkeys([
                    *decision.get("diagnostics", []),
                    *model_decision["diagnostics"],
                ]))
        return decision

    if model_decision and model_decision["next_action"] in {_REPLAN_API, _REPLAN_UI}:
        target_stage = "api" if model_decision["next_action"] == _REPLAN_API else "ui"
        if target_stage == stage and _can_replan(state, stage):
            return model_decision

    if model_decision and model_decision["next_action"] == _CONTINUE_TO_UI and stage == "api":
        return model_decision

    if model_decision and model_decision["next_action"] == _REPORT and guardrail["next_action"] == _REPORT:
        return model_decision

    return guardrail


def _append_evaluation(state: AgentState, evaluation: dict[str, Any]) -> None:
    evaluations = state.setdefault("agent_evaluations", [])
    evaluations.append(redact_sensitive_data(evaluation))
    if len(evaluations) > _MAX_EVALUATIONS:
        del evaluations[:-_MAX_EVALUATIONS]
    state["evidence_evaluation"] = evaluations[-1]


def _append_attempt_history(state: AgentState, stage: str, summary: dict[str, Any]) -> None:
    attempts = state.setdefault("agent_attempt_history", [])
    attempts.append(
        redact_sensitive_data(
            {
                "stage": stage,
                "summary": summary.get(stage) or {},
                "replan_count": _replan_count(state, stage) + 1,
            }
        )
    )
    if len(attempts) > _MAX_EVALUATIONS:
        del attempts[:-_MAX_EVALUATIONS]


def _increment_replan_count(state: AgentState, stage: str) -> None:
    counts = dict(state.get("agent_replan_counts") or {})
    counts[stage] = _safe_int(counts.get(stage)) + 1
    state["agent_replan_counts"] = counts


def _apply_decision(state: AgentState, stage: str, decision: dict[str, Any], summary: dict[str, Any]) -> str:
    action = str(decision.get("next_action") or _REPORT)
    if action == _REPLAN_API:
        _append_attempt_history(state, "api", summary)
        _increment_replan_count(state, "api")
        state["api_cases"] = []
        state["test_cases"] = list(_safe_list(state.get("ui_cases")))
        state["agent_replan_feedback"] = decision.get("replan_instructions") or decision.get("reason")
    elif action == _REPLAN_UI:
        _append_attempt_history(state, "ui", summary)
        _increment_replan_count(state, "ui")
        state["ui_cases"] = []
        state["test_cases"] = list(_safe_list(state.get("api_cases")))
        state["agent_replan_feedback"] = decision.get("replan_instructions") or decision.get("reason")
    elif action in {_CONTINUE_TO_UI, _REPORT}:
        state["agent_replan_feedback"] = None

    next_node = _ACTION_TO_NODE.get(action, "reporter")
    state["agent_next_node"] = next_node
    return next_node


async def run(state: AgentState) -> AgentState:
    install_tool_context(state)
    stage = _stage_from_state(state)
    allowed = _allowed_actions(state, stage)
    summary = _evidence_summary(state, stage)
    tool_calls = _tool_call_summary(state)
    model_raw, model_error = await _model_decision(state, stage, summary, tool_calls, allowed)
    model_decision = _normalize_model_decision(model_raw, allowed)
    guardrail = _guardrail_decision(state, stage, summary, allowed)
    decision = _merge_decisions(
        guardrail=guardrail,
        model_decision=model_decision,
        state=state,
        stage=stage,
    )
    next_node = _apply_decision(state, stage, decision, summary)

    evaluation = {
        "stage": stage,
        "allowed_actions": allowed,
        "sufficient_evidence": bool(decision.get("sufficient_evidence")),
        "confidence": decision.get("confidence") or "medium",
        "next_action": decision.get("next_action") or _REPORT,
        "next_node": next_node,
        "reason": decision.get("reason") or "Execution evidence evaluated.",
        "diagnostics": decision.get("diagnostics") or [],
        "missing_evidence": decision.get("missing_evidence") or [],
        "replan_instructions": decision.get("replan_instructions") or "",
        "source": decision.get("source") or "guardrail",
        "model_error": model_error,
        "summary": summary,
    }
    _append_evaluation(state, evaluation)

    record_tool_call(
        state,
        tool_name="planner.evaluate_execution_evidence",
        layer="planner",
        status="success",
        input_summary={
            "stage": stage,
            "allowed_actions": allowed,
            "api_executed": summary["api"]["executed"],
            "ui_command_completed": summary["ui"]["command_completed"],
        },
        output_summary={
            "next_action": evaluation["next_action"],
            "next_node": next_node,
            "sufficient_evidence": evaluation["sufficient_evidence"],
            "source": evaluation["source"],
            "model_error": model_error,
        },
    )

    detail = (
        f"Evidence evaluation: {evaluation['next_action']} "
        f"({evaluation['reason']})"
    )
    status = "running" if next_node != "reporter" else "done"
    state.setdefault("workflow_steps", []).append(
        {"node": "execution_evaluator", "status": status, "detail": detail}
    )
    await persist_progress(state, "execution_evaluator", status, detail)
    return state


def route_after_evaluation(state: AgentState) -> str:
    next_node = str(state.get("agent_next_node") or "reporter")
    return next_node if next_node in {"tc_generator", "ui_test_planner", "ui_login", "reporter"} else "reporter"

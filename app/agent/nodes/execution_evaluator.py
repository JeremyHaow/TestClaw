from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage

from app.agent.api_scope import (
    ALL_SAFE_GET_COVERAGE_GOAL,
    ALL_SAFE_GET_COVERAGE_SOURCE,
    documented_api_scope_text,
)
from app.agent.action_runtime import append_evaluation_protocol
from app.agent.json_utils import parse_llm_json_object
from app.agent.progress import persist_progress
from app.agent.prompts import EVIDENCE_EVALUATOR_PROMPT
from app.agent.state import AgentState
from app.agent.strategy import STRATEGY_SCHEMA_SOURCE, strategy_summary
from app.agent.tool_registry import install_tool_context, record_tool_call
from app.config import settings
from app.core.llm_gateway import ainvoke_with_timeout, llm_gateway
from app.core.redaction import redact_sensitive_data

logger = logging.getLogger(__name__)

_REPORT = "report"
_CONTINUE = "continue"
_CONTINUE_TO_UI = "continue_to_ui"
_RETRY_SAME_ACTION = "retry_same_action"
_REPLAN_API = "replan_api"
_REPLAN_UI = "replan_ui"
_ASK_HUMAN = "ask_human"
_ACTION_TO_NODE = {
    _REPORT: "reporter",
    _CONTINUE: "reporter",
    _CONTINUE_TO_UI: "ui_login",
    _REPLAN_API: "tc_generator",
    _REPLAN_UI: "ui_test_planner",
    _ASK_HUMAN: "reporter",
}
_GUARDRAIL_STOP_ACTIONS = {
    _RETRY_SAME_ACTION,
    _REPLAN_API,
    _REPLAN_UI,
    _ASK_HUMAN,
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
    return parse_llm_json_object(content)


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
        "coverage_goal": (
            (result.get("request_selection") or state.get("api_request_selection") or {}).get("coverage_goal")
            or state.get("api_coverage_goal")
        ),
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


def _stage_observations(state: AgentState, stage: str) -> list[dict[str, Any]]:
    stage_names = {stage, f"{stage}_runner"}
    return [
        item
        for item in _safe_list(state.get("agent_observations"))
        if isinstance(item, dict)
        and (item.get("stage") in stage_names or item.get("layer") == stage)
    ]


def _stage_evidence_kinds(state: AgentState, observations: list[dict[str, Any]]) -> dict[str, int]:
    evidence_by_id = {
        item.get("evidence_id"): item
        for item in _safe_list(state.get("agent_evidence"))
        if isinstance(item, dict) and item.get("evidence_id")
    }
    kinds: dict[str, int] = {}
    for observation in observations:
        for evidence_id in _safe_list(observation.get("evidence_ids")):
            evidence = evidence_by_id.get(evidence_id)
            kind = str((evidence or {}).get("kind") or "unknown")
            kinds[kind] = kinds.get(kind, 0) + 1
    return kinds


def _dominant_failure_type(stage_summary: dict[str, Any]) -> str | None:
    failure_types = stage_summary.get("failure_types")
    if not isinstance(failure_types, dict) or not failure_types:
        return None
    return max(failure_types.items(), key=lambda item: int(item[1] or 0))[0]


def _latest_failure_type(observations: list[dict[str, Any]]) -> str | None:
    for observation in reversed(observations):
        failure_type = observation.get("failure_type")
        if failure_type:
            return str(failure_type)
    return None


def _stage_failure_type(stage_summary: dict[str, Any]) -> str | None:
    return (
        stage_summary.get("latest_failure_type")
        or stage_summary.get("dominant_failure_type")
        or _dominant_failure_type(stage_summary)
    )


def _observation_output(observation: dict[str, Any]) -> dict[str, Any]:
    output = observation.get("outputs")
    return output if isinstance(output, dict) else {}


def _observation_input(observation: dict[str, Any]) -> dict[str, Any]:
    inputs = observation.get("inputs")
    return inputs if isinstance(inputs, dict) else {}


def _protocol_observation_summary(state: AgentState, stage: str) -> dict[str, Any]:
    observations = _stage_observations(state, stage)
    failure_types: dict[str, int] = {}
    statuses: dict[str, int] = {}
    outcomes: dict[str, int] = {}
    latest = []
    failed = 0
    blocked = 0
    executed = 0
    for observation in observations:
        status = str(observation.get("status") or "unknown")
        statuses[status] = statuses.get(status, 0) + 1
        outcome = str(observation.get("outcome") or "unknown")
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        if outcome in {"failed", "blocked"} or status in {"failed", "blocked"}:
            failed += 1
        if outcome == "blocked" or status == "blocked":
            blocked += 1
        if status not in {"skipped", "blocked", "unknown"}:
            executed += 1
        failure_type = observation.get("failure_type")
        if failure_type:
            key = str(failure_type)
            failure_types[key] = failure_types.get(key, 0) + 1
    for observation in observations[-6:]:
        inputs = _observation_input(observation)
        outputs = _observation_output(observation)
        latest.append(
            {
                "tool_name": observation.get("tool_name"),
                "status": observation.get("status"),
                "outcome": observation.get("outcome"),
                "failure_type": observation.get("failure_type"),
                "summary": _compact_text(observation.get("summary"), 240),
                "evidence_count": len(_safe_list(observation.get("evidence_ids"))),
                "method": inputs.get("method"),
                "path": inputs.get("path"),
                "command": inputs.get("command"),
                "status_code": outputs.get("status_code"),
                "error_type": outputs.get("error_type"),
                "safety_decision": outputs.get("safety_decision"),
            }
        )
    return {
        "observation_count": len(observations),
        "executed_count": executed,
        "failed_count": failed,
        "blocked_count": blocked,
        "statuses": statuses,
        "outcomes": outcomes,
        "failure_types": failure_types,
        "dominant_failure_type": _dominant_failure_type({"failure_types": failure_types}),
        "latest_failure_type": _latest_failure_type(observations),
        "evidence_kinds": _stage_evidence_kinds(state, observations),
        "latest": latest,
    }


def _evidence_summary(state: AgentState, stage: str) -> dict[str, Any]:
    protocol = state.get("agent_protocol_summary") or {}
    protocol_stage = _protocol_observation_summary(state, stage)
    summary = redact_sensitive_data(
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
            "agent_strategy": strategy_summary(state.get("agent_strategy_decision")),
            "agent_protocol": protocol,
            "agent_protocol_stage": protocol_stage,
            "agent_observation_count": len(_safe_list(state.get("agent_observations"))),
            "replan_counts": state.get("agent_replan_counts") or {},
            "retry_counts": state.get("agent_retry_counts") or {},
            "last_error": _compact_text(state.get("last_error"), 300),
        }
    )
    if isinstance(summary.get("agent_protocol"), dict) and isinstance(protocol, dict):
        if isinstance(protocol.get("by_failure_type"), dict):
            summary["agent_protocol"]["by_failure_type"] = protocol.get("by_failure_type")
    if isinstance(summary.get("agent_protocol_stage"), dict):
        for key in ("failure_types", "dominant_failure_type", "latest_failure_type"):
            summary["agent_protocol_stage"][key] = protocol_stage.get(key)
    return summary


def _allowed_actions(state: AgentState, stage: str) -> list[str]:
    if stage == "api":
        actions = [_REPORT, _CONTINUE, _RETRY_SAME_ACTION, _REPLAN_API, _ASK_HUMAN]
        if _has_ui_target(state):
            actions.insert(2, _CONTINUE_TO_UI)
        return actions
    return [_REPORT, _CONTINUE, _RETRY_SAME_ACTION, _REPLAN_UI, _ASK_HUMAN]


def _replan_count(state: AgentState, stage: str) -> int:
    counts = state.get("agent_replan_counts")
    if isinstance(counts, dict):
        return _safe_int(counts.get(stage))
    return 0


def _can_replan(state: AgentState, stage: str) -> bool:
    return _replan_count(state, stage) < max(0, int(settings.AGENT_MAX_REPLAN_ATTEMPTS))


def _retry_count(state: AgentState, stage: str) -> int:
    counts = state.get("agent_retry_counts")
    if isinstance(counts, dict):
        return _safe_int(counts.get(stage))
    return 0


def _can_retry(state: AgentState, stage: str) -> bool:
    return _retry_count(state, stage) < max(0, int(settings.AGENT_MAX_REPLAN_ATTEMPTS))


def _auth_context_available(state: AgentState) -> bool:
    config = state.get("auth_config")
    config_enabled = isinstance(config, dict) and bool(config.get("enabled"))
    return bool(config_enabled or state.get("auth_headers") or state.get("auth_credentials"))


def _decision_payload(
    *,
    sufficient_evidence: bool,
    confidence: str,
    next_action: str,
    reason: str,
    diagnostics: list[str] | None = None,
    missing_evidence: list[str] | None = None,
    replan_instructions: str = "",
    failure_type: str | None = None,
    human_question: str = "",
    source: str = "guardrail",
) -> dict[str, Any]:
    missing = missing_evidence or []
    return {
        "sufficient_evidence": sufficient_evidence,
        "confidence": confidence,
        "next_action": next_action,
        "reason": reason,
        "diagnostics": diagnostics or [],
        "missing_evidence": missing,
        "replan_instructions": replan_instructions,
        "replan_hint": replan_instructions,
        "failure_type": failure_type,
        "human_question": human_question,
        "source": source,
    }


def _api_needs_replan(state: AgentState, summary: dict[str, Any]) -> tuple[bool, str, list[str]]:
    api = summary["api"]
    protocol = summary.get("agent_protocol_stage") or {}
    failure_types = protocol.get("failure_types") if isinstance(protocol, dict) else {}
    dominant_failure = _stage_failure_type(protocol if isinstance(protocol, dict) else {})
    if not api["requested"]:
        return False, "API stage is not requested for this run.", []
    if _api_has_completed_strategy_coverage(state, summary):
        return False, "Validated agent strategy coverage reached a reportable stopping point.", []
    if api["all_passed"] and api["executed"] > 0:
        return False, "Current API execution passed; older failed observations do not require replanning.", []
    if isinstance(failure_types, dict) and failure_types:
        if dominant_failure == "dependency_missing":
            return True, "API observations show missing upstream dependency evidence.", [
                "需要重新生成包含上游列表/搜索/创建步骤的 API 请求链，避免发送合成占位路径参数。"
            ]
        if dominant_failure == "safe_write_blocked" and api["executed"] == 0:
            return True, "API observations were blocked by safe-write guardrails before execution.", [
                "下一轮必须显式使用安全写入闸门，或改为执行策略允许的只读端点。"
            ]
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


def _api_needs_retry(state: AgentState, summary: dict[str, Any]) -> tuple[bool, str, list[str], str | None]:
    api = summary["api"]
    if api["all_passed"] and api["executed"] > 0:
        return False, "Current API execution passed; older transient failures do not require retry.", [], None
    protocol = summary.get("agent_protocol_stage") or {}
    failure_type = _stage_failure_type(protocol if isinstance(protocol, dict) else {})
    if failure_type not in {"network_error", "timeout"}:
        return False, "API observations do not indicate a transient transport failure.", [], None
    if not _can_retry(state, "api"):
        return False, "API retry limit reached.", [
            "已达到 API 同动作重试上限，报告中保留网络/超时诊断。"
        ], failure_type
    return True, "API observations indicate a transient transport failure.", [
        "重试同一批 API 请求以确认网络错误或超时是否可复现。"
    ], failure_type


def _api_needs_human(state: AgentState, summary: dict[str, Any]) -> tuple[bool, str, list[str], str | None]:
    api = summary["api"]
    if api["all_passed"] and api["executed"] > 0:
        return False, "Current API execution passed; older failures do not require human intervention.", [], None
    protocol = summary.get("agent_protocol_stage") or {}
    failure_type = _stage_failure_type(protocol if isinstance(protocol, dict) else {})
    if failure_type == "auth_failure" and not _auth_context_available(state):
        return True, "API observations failed at authentication and no usable auth context is configured.", [
            "需要用户提供有效 token、cookie、登录配置，或明确确认该接口应在未登录状态下返回 401/403。"
        ], failure_type
    if failure_type == "environment_blocked":
        return True, "API observations indicate the target environment blocked execution.", [
            "需要用户确认测试环境、base URL、网络访问或接口方法是否可在当前环境执行。"
        ], failure_type
    return False, "No API human intervention requirement was detected.", [], failure_type


def _api_has_schema_driven_all_safe_coverage(state: AgentState, summary: dict[str, Any]) -> bool:
    api = summary["api"]
    selection = api.get("request_selection") or {}
    if selection.get("source") == ALL_SAFE_GET_COVERAGE_SOURCE:
        return api["total"] > 0 and selection.get("coverage_goal") == ALL_SAFE_GET_COVERAGE_GOAL
    return False


def _api_has_completed_strategy_coverage(state: AgentState, summary: dict[str, Any]) -> bool:
    api = summary["api"]
    selection = api.get("request_selection") or {}
    strategy = state.get("agent_strategy_decision") or {}
    coverage_scope = selection.get("coverage_scope") or strategy.get("coverage_scope")
    if _api_has_schema_driven_all_safe_coverage(state, summary):
        return True
    if selection.get("source") != STRATEGY_SCHEMA_SOURCE:
        return False
    if coverage_scope not in {"focused_documented_endpoints", "sampled_contract"}:
        return False
    return api["total"] > 0 and bool(selection.get("strategy_coverage_completed", True))


def _api_has_reportable_schema_evidence(state: AgentState, summary: dict[str, Any]) -> bool:
    api = summary["api"]
    selection = api.get("request_selection") or {}
    source = selection.get("source")
    if source not in {
        ALL_SAFE_GET_COVERAGE_SOURCE,
        STRATEGY_SCHEMA_SOURCE,
        "parsed_api_schema",
        "safe_schema_fallback",
    }:
        return False
    safe_count = max(api.get("safe_schema_endpoint_count") or 0, 1)
    minimum = min(10, safe_count)
    return api["total"] > 0 and api["http_executed"] >= minimum


def _ui_needs_replan(state: AgentState, summary: dict[str, Any]) -> tuple[bool, str, list[str]]:
    ui = summary["ui"]
    protocol = summary.get("agent_protocol_stage") or {}
    dominant_failure = _stage_failure_type(protocol if isinstance(protocol, dict) else {})
    if not ui["requested"]:
        return False, "UI stage is not requested for this run.", []
    if ui["all_passed"] and ui["completed"] > 0:
        return False, "Current UI execution passed; older failed observations do not require replanning.", []
    if ui["setup_failed"]:
        return False, "UI setup failed and requires user intervention before more automation.", []
    if str(state.get("source_input") or "").strip().lower() == "suite":
        return False, "Selected suite cases preserve user-provided execution semantics.", []
    if ui["total"] == 0:
        return True, "UI stage produced no executable UI cases.", [
            "基于当前页面快照重新生成可执行 UI 用例。"
        ]
    if dominant_failure in {"ui_locator_missing", "ui_assertion_failure"} and ui["has_snapshot_context"]:
        return True, "UI observations show a locator/assertion failure while snapshot context is available.", [
            "基于最新 snapshot/ref 重新生成 UI 动作，避免重复失败选择器。"
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


def _ui_needs_retry(state: AgentState, summary: dict[str, Any]) -> tuple[bool, str, list[str], str | None]:
    ui = summary["ui"]
    if ui["all_passed"] and ui["completed"] > 0:
        return False, "Current UI execution passed; older transient failures do not require retry.", [], None
    protocol = summary.get("agent_protocol_stage") or {}
    failure_type = _stage_failure_type(protocol if isinstance(protocol, dict) else {})
    if failure_type not in {"timeout", "navigation_blocked"}:
        return False, "UI observations do not indicate a transient browser failure.", [], None
    if not _can_retry(state, "ui"):
        return False, "UI retry limit reached.", [
            "已达到 UI 同动作重试上限，报告中保留超时/导航阻塞诊断。"
        ], failure_type
    return True, "UI observations indicate a transient browser failure.", [
        "重试同一批 UI 命令以确认超时或导航阻塞是否可复现。"
    ], failure_type


def _ui_needs_human(state: AgentState, summary: dict[str, Any]) -> tuple[bool, str, list[str], str | None]:
    ui = summary["ui"]
    if ui["all_passed"] and ui["completed"] > 0:
        return False, "Current UI execution passed; older failures do not require human intervention.", [], None
    protocol = summary.get("agent_protocol_stage") or {}
    failure_type = _stage_failure_type(protocol if isinstance(protocol, dict) else {})
    if ui["setup_failed"]:
        return True, "UI setup/login failed and requires user intervention before more automation.", [
            "需要用户补充登录步骤、验证码/MFA 处理方式，或确认无需登录即可继续。"
        ], failure_type or "ui_setup_failed"
    if failure_type == "ui_high_risk_action_blocked":
        return True, "UI observations include a blocked high-risk browser action.", [
            "需要用户确认是否允许该高风险浏览器动作，或提供安全的替代测试路径。"
        ], failure_type
    return False, "No UI human intervention requirement was detected.", [], failure_type


def _guardrail_decision(
    state: AgentState,
    stage: str,
    summary: dict[str, Any],
    allowed_actions: list[str],
) -> dict[str, Any]:
    if stage == "api":
        needs_human, human_reason, human_missing, human_failure = _api_needs_human(state, summary)
        if needs_human and _ASK_HUMAN in allowed_actions:
            return _decision_payload(
                sufficient_evidence=False,
                confidence="high",
                next_action=_ASK_HUMAN,
                reason=human_reason,
                diagnostics=human_missing,
                missing_evidence=human_missing,
                failure_type=human_failure,
                human_question=human_missing[0] if human_missing else human_reason,
            )

        needs_retry, retry_reason, retry_missing, retry_failure = _api_needs_retry(state, summary)
        if needs_retry and _RETRY_SAME_ACTION in allowed_actions:
            return _decision_payload(
                sufficient_evidence=False,
                confidence="medium",
                next_action=_RETRY_SAME_ACTION,
                reason=retry_reason,
                diagnostics=retry_missing,
                missing_evidence=retry_missing,
                replan_instructions="Retry the same API request batch once before changing the plan.",
                failure_type=retry_failure,
            )
        if retry_failure in {"network_error", "timeout"} and retry_missing:
            return _decision_payload(
                sufficient_evidence=False,
                confidence="medium",
                next_action=_REPORT,
                reason=f"{retry_reason} Retry limit reached.",
                diagnostics=retry_missing,
                missing_evidence=retry_missing,
                failure_type=retry_failure,
            )

        needs_replan, reason, missing = _api_needs_replan(state, summary)
        failure_type = _stage_failure_type(summary.get("agent_protocol_stage") or {})
        if needs_replan and _REPLAN_API in allowed_actions and _can_replan(state, "api"):
            return _decision_payload(
                sufficient_evidence=False,
                confidence="medium",
                next_action=_REPLAN_API,
                reason=reason,
                diagnostics=missing,
                missing_evidence=missing,
                replan_instructions="Regenerate API cases from available schema/base URL and favor safe executable probes before reporting.",
                failure_type=failure_type,
            )
        if needs_replan and not _can_replan(state, "api"):
            return _decision_payload(
                sufficient_evidence=False,
                confidence="medium",
                next_action=_REPORT,
                reason=f"{reason} Replan limit reached.",
                diagnostics=[*missing, "已达到 API 重规划上限，报告中保留阻塞诊断。"],
                missing_evidence=missing,
                failure_type=failure_type,
            )
        if _CONTINUE_TO_UI in allowed_actions:
            return _decision_payload(
                sufficient_evidence=True,
                confidence="medium",
                next_action=_CONTINUE_TO_UI,
                reason="API stage is complete enough; continuing to requested UI coverage.",
            )
        return _decision_payload(
            sufficient_evidence=True,
            confidence="medium",
            next_action=_REPORT,
            reason="API stage reached a reportable stopping point.",
        )

    needs_human, human_reason, human_missing, human_failure = _ui_needs_human(state, summary)
    if needs_human and _ASK_HUMAN in allowed_actions:
        return _decision_payload(
            sufficient_evidence=False,
            confidence="high",
            next_action=_ASK_HUMAN,
            reason=human_reason,
            diagnostics=human_missing,
            missing_evidence=human_missing,
            failure_type=human_failure,
            human_question=human_missing[0] if human_missing else human_reason,
        )

    needs_retry, retry_reason, retry_missing, retry_failure = _ui_needs_retry(state, summary)
    if needs_retry and _RETRY_SAME_ACTION in allowed_actions:
        return _decision_payload(
            sufficient_evidence=False,
            confidence="medium",
            next_action=_RETRY_SAME_ACTION,
            reason=retry_reason,
            diagnostics=retry_missing,
            missing_evidence=retry_missing,
            replan_instructions="Retry the same UI command batch once before changing the plan.",
            failure_type=retry_failure,
        )
    if retry_failure in {"timeout", "navigation_blocked"} and retry_missing:
        return _decision_payload(
            sufficient_evidence=False,
            confidence="medium",
            next_action=_REPORT,
            reason=f"{retry_reason} Retry limit reached.",
            diagnostics=retry_missing,
            missing_evidence=retry_missing,
            failure_type=retry_failure,
        )

    needs_replan, reason, missing = _ui_needs_replan(state, summary)
    failure_type = _stage_failure_type(summary.get("agent_protocol_stage") or {})
    if needs_replan and _REPLAN_UI in allowed_actions and _can_replan(state, "ui"):
        return _decision_payload(
            sufficient_evidence=False,
            confidence="medium",
            next_action=_REPLAN_UI,
            reason=reason,
            diagnostics=missing,
            missing_evidence=missing,
            replan_instructions="Regenerate UI cases from the latest snapshot evidence and avoid repeating failed selectors.",
            failure_type=failure_type,
        )
    if needs_replan and not _can_replan(state, "ui"):
        return _decision_payload(
            sufficient_evidence=False,
            confidence="medium",
            next_action=_REPORT,
            reason=f"{reason} Replan limit reached.",
            diagnostics=[*missing, "已达到 UI 重规划上限，报告中保留阻塞诊断。"],
            missing_evidence=missing,
            failure_type=failure_type,
        )
    return _decision_payload(
        sufficient_evidence=True,
        confidence="medium",
        next_action=_REPORT,
        reason="UI stage reached a reportable stopping point.",
    )


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
            mission_plan=json.dumps(
                {
                    "control_pattern": (state.get("agent_mission_plan") or {}).get("control_pattern"),
                    "subgoals": (state.get("agent_mission_plan") or {}).get("subgoals", [])[:8],
                    "success_criteria": (state.get("agent_mission_plan") or {}).get("success_criteria", [])[:5],
                    "delegation": (state.get("agent_delegation_trace") or [])[-8:],
                },
                ensure_ascii=False,
                default=str,
            )[:5000],
            evidence_summary=json.dumps(summary, ensure_ascii=False, default=str)[:6000],
            tool_call_summary=json.dumps(tool_calls, ensure_ascii=False, default=str)[:4000],
            prior_evaluations=json.dumps(
                _safe_list(state.get("agent_evaluations"))[-4:],
                ensure_ascii=False,
                default=str,
            )[:3000],
            allowed_actions=", ".join(allowed_actions),
        )
        resp = await ainvoke_with_timeout(
            llm,
            [HumanMessage(content=prompt)],
            call_name="execution_evaluator.evaluate_evidence",
        )
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
    if action == _CONTINUE and _CONTINUE_TO_UI in allowed_actions:
        action = _CONTINUE_TO_UI
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
        "replan_hint": _compact_text(
            model_decision.get("replan_hint") or model_decision.get("replan_instructions"),
            800,
        ),
        "failure_type": _compact_text(model_decision.get("failure_type"), 160) or None,
        "human_question": _compact_text(model_decision.get("human_question"), 500),
        "source": "llm",
    }


def _sanitize_api_replan_instructions(state: AgentState, value: Any) -> str:
    raw = _compact_text(value, 500)
    allowed_paths = {
        str(endpoint.get("path") or "").strip().rstrip("/") or "/"
        for endpoint in _safe_list(state.get("parsed_api_schema"))
        if isinstance(endpoint, dict) and endpoint.get("path")
    }
    for path in set(re.findall(r"/[A-Za-z0-9_./{}:-]+", raw)):
        normalized = path.rstrip("/") or "/"
        if normalized not in allowed_paths:
            raw = raw.replace(path, "[out-of-scope-path-removed]")
    policy = str(state.get("api_execution_policy") or "safe_read_only").strip().lower()
    scope = documented_api_scope_text(
        _safe_list(state.get("parsed_api_schema")),
        execution_policy=policy,
    )
    parts = [
        scope,
        (
            "Regenerate only documented API cases. Do not add non-existent paths, "
            "out-of-schema negative probes, auth-bypass tests, or mutation methods "
            "blocked by the execution policy. Deeper assertions must be grounded in "
            "the documented response schema or kept advisory/non-blocking."
        ),
    ]
    if raw:
        parts.append(f"Evaluator intent, bounded by this scope: {raw}")
    return " ".join(parts)[:900]


def _sanitize_replan_instructions(
    state: AgentState,
    stage: str,
    decision: dict[str, Any],
) -> dict[str, Any]:
    action = decision.get("next_action")
    if stage == "api" and action == _REPLAN_API:
        sanitized = dict(decision)
        sanitized["replan_instructions"] = _sanitize_api_replan_instructions(
            state,
            sanitized.get("replan_instructions") or sanitized.get("reason"),
        )
        return sanitized
    return decision


def _merge_decisions(
    *,
    guardrail: dict[str, Any],
    model_decision: dict[str, Any] | None,
    state: AgentState,
    stage: str,
) -> dict[str, Any]:
    if guardrail["next_action"] in _GUARDRAIL_STOP_ACTIONS:
        decision = dict(guardrail)
        if model_decision:
            decision["source"] = "llm+guardrail"
            if model_decision.get("replan_instructions"):
                decision["replan_instructions"] = model_decision["replan_instructions"]
            if model_decision.get("replan_hint"):
                decision["replan_hint"] = model_decision["replan_hint"]
            if model_decision.get("diagnostics"):
                decision["diagnostics"] = list(dict.fromkeys([
                    *decision.get("diagnostics", []),
                    *model_decision["diagnostics"],
                ]))
            if not decision.get("failure_type") and model_decision.get("failure_type"):
                decision["failure_type"] = model_decision["failure_type"]
            if not decision.get("human_question") and model_decision.get("human_question"):
                decision["human_question"] = model_decision["human_question"]
        return _sanitize_replan_instructions(state, stage, decision)

    if guardrail.get("sufficient_evidence") is False:
        return guardrail

    if model_decision and model_decision["next_action"] in {_REPLAN_API, _REPLAN_UI}:
        summary = _evidence_summary(state, stage)
        if stage == "api" and (
            _api_has_completed_strategy_coverage(state, summary)
            or _api_has_reportable_schema_evidence(state, summary)
        ):
            decision = dict(guardrail)
            decision["source"] = "guardrail"
            decision["reason"] = (
                "API stage already has reportable schema execution evidence; "
                "model-requested replan was suppressed to avoid repeated shallow replanning."
            )
            decision["diagnostics"] = list(dict.fromkeys([
                *decision.get("diagnostics", []),
                "模型建议重规划，但本轮已经有可报告的 schema 执行证据，已停止继续重规划。",
            ]))
            return decision
        target_stage = "api" if model_decision["next_action"] == _REPLAN_API else "ui"
        if target_stage == stage and _can_replan(state, stage):
            return _sanitize_replan_instructions(state, stage, model_decision)

    if model_decision and model_decision["next_action"] == _RETRY_SAME_ACTION and _can_retry(state, stage):
        return model_decision

    if model_decision and model_decision["next_action"] == _ASK_HUMAN:
        return model_decision

    if model_decision and model_decision["next_action"] == _CONTINUE_TO_UI and stage == "api":
        return model_decision

    if model_decision and model_decision["next_action"] == _CONTINUE and guardrail.get("sufficient_evidence"):
        return {
            **model_decision,
            "next_action": guardrail.get("next_action", _REPORT),
            "source": model_decision.get("source") or "llm",
        }

    if model_decision and model_decision["next_action"] == _REPORT and guardrail["next_action"] == _REPORT:
        return model_decision

    return guardrail


def _append_evaluation(state: AgentState, evaluation: dict[str, Any]) -> None:
    evaluations = state.setdefault("agent_evaluations", [])
    evaluations.append(redact_sensitive_data(evaluation))
    if len(evaluations) > _MAX_EVALUATIONS:
        del evaluations[:-_MAX_EVALUATIONS]
    state["evidence_evaluation"] = evaluations[-1]


def _append_attempt_history(
    state: AgentState,
    stage: str,
    summary: dict[str, Any],
    *,
    attempt_kind: str = "replan",
) -> None:
    attempts = state.setdefault("agent_attempt_history", [])
    attempts.append(
        redact_sensitive_data(
            {
                "stage": stage,
                "attempt_kind": attempt_kind,
                "summary": summary.get(stage) or {},
                "replan_count": _replan_count(state, stage)
                + (1 if attempt_kind == "replan" else 0),
                "retry_count": _retry_count(state, stage)
                + (1 if attempt_kind == "retry" else 0),
            }
        )
    )
    if len(attempts) > _MAX_EVALUATIONS:
        del attempts[:-_MAX_EVALUATIONS]


def _increment_replan_count(state: AgentState, stage: str) -> None:
    counts = dict(state.get("agent_replan_counts") or {})
    counts[stage] = _safe_int(counts.get(stage)) + 1
    state["agent_replan_counts"] = counts


def _increment_retry_count(state: AgentState, stage: str) -> None:
    counts = dict(state.get("agent_retry_counts") or {})
    counts[stage] = _safe_int(counts.get(stage)) + 1
    state["agent_retry_counts"] = counts


def _apply_decision(state: AgentState, stage: str, decision: dict[str, Any], summary: dict[str, Any]) -> str:
    action = str(decision.get("next_action") or _REPORT)
    if action == _REPLAN_API:
        _append_attempt_history(state, "api", summary, attempt_kind="replan")
        _increment_replan_count(state, "api")
        state["api_cases"] = []
        state["test_cases"] = list(_safe_list(state.get("ui_cases")))
        state["agent_replan_feedback"] = decision.get("replan_instructions") or decision.get("reason")
        state["agent_retry_feedback"] = None
        state["agent_human_question"] = None
    elif action == _REPLAN_UI:
        _append_attempt_history(state, "ui", summary, attempt_kind="replan")
        _increment_replan_count(state, "ui")
        state["ui_cases"] = []
        state["test_cases"] = list(_safe_list(state.get("api_cases")))
        state["agent_replan_feedback"] = decision.get("replan_instructions") or decision.get("reason")
        state["agent_retry_feedback"] = None
        state["agent_human_question"] = None
    elif action == _RETRY_SAME_ACTION:
        _append_attempt_history(state, stage, summary, attempt_kind="retry")
        _increment_retry_count(state, stage)
        state["agent_replan_feedback"] = None
        state["agent_retry_feedback"] = (
            decision.get("replan_instructions")
            or decision.get("replan_hint")
            or decision.get("reason")
        )
        state["agent_human_question"] = None
    elif action == _ASK_HUMAN:
        state["agent_replan_feedback"] = None
        state["agent_retry_feedback"] = None
        state["agent_human_question"] = (
            decision.get("human_question")
            or (decision.get("missing_evidence") or [None])[0]
            or decision.get("reason")
        )
    elif action in {_CONTINUE_TO_UI, _CONTINUE, _REPORT}:
        state["agent_replan_feedback"] = None
        state["agent_retry_feedback"] = None
        state["agent_human_question"] = None

    if action == _RETRY_SAME_ACTION:
        next_node = "api_runner" if stage == "api" else "ui_runner"
    else:
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
        "replan_hint": decision.get("replan_hint") or decision.get("replan_instructions") or "",
        "failure_type": decision.get("failure_type"),
        "human_question": decision.get("human_question") or state.get("agent_human_question"),
        "source": decision.get("source") or "guardrail",
        "model_error": model_error,
        "summary": summary,
    }
    _append_evaluation(state, evaluation)
    append_evaluation_protocol(state, evaluation, stage=stage)

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
            "failure_type": evaluation["failure_type"],
            "source": evaluation["source"],
            "model_error": model_error,
        },
        metadata={
            "reason": "Evaluate visible evidence against mission success criteria and decide whether to continue, replan, or report.",
            "next_decision": evaluation["next_action"],
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
    return next_node if next_node in {
        "api_runner",
        "tc_generator",
        "ui_runner",
        "ui_test_planner",
        "ui_login",
        "reporter",
    } else "reporter"

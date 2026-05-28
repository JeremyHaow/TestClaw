import logging

from sqlalchemy import select

from app.agent.progress import persist_progress
from app.agent.runtime.failure_taxonomy import report_category_for_failure
from app.agent.state import AgentState
from app.agent.tool_registry import record_tool_call, summarize_tool_calls
from app.agent.api_scope import ALL_SAFE_GET_COVERAGE_SOURCE
from app.core.redaction import redact_sensitive_data
from app.models.run_artifacts import RunFinding

logger = logging.getLogger(__name__)


def _summary_counts(result: dict | None) -> dict:
    if not result:
        return {"total": 0, "executed": 0, "passed": 0, "failed": 0, "skipped": 0, "pass_rate": "0%"}
    total = int(result.get("total") or 0)
    passed = int(result.get("passed") or 0)
    skipped = int(result.get("skipped") or 0)
    executed = int(result.get("executed") or max(total - skipped, 0))
    failed = result.get("failed")
    failed_count = int(failed) if failed is not None else max(executed - passed, 0)
    return {
        "total": total,
        "executed": executed,
        "passed": passed,
        "failed": failed_count,
        "skipped": skipped,
        "pass_rate": f"{round(passed / executed * 100, 1)}%" if executed else "0%",
    }


def _strip_terminal_punctuation(value: str | None) -> str:
    return str(value or "").strip().rstrip("。.!?！？")


def _has_api_target(state: AgentState) -> bool:
    return bool(
        state.get("parsed_api_schema")
        or state.get("api_cases")
        or state.get("base_url_override")
        or (state.get("test_type") or "").lower() in {"api", "full"}
    )


def _collect_failure_details(state: AgentState) -> list[dict]:
    """Collect failures caused by actual API/UI execution, not normalized syntax repair."""
    failures = []

    setup_required = bool((state.get("setup_instructions") or state.get("login_instructions") or "").strip())
    login_verified = state.get("login_verified")
    setup_result = state.get("setup_result") or state.get("login_result") or {}
    if setup_required and setup_result.get("required") and login_verified is False:
        failures.append(
            {
                "source": "setup",
                "title": "UI pre-test setup verification failed",
                "reason": state.get("login_verification_reason") or state.get("last_error") or "Pre-test setup could not be verified.",
                "screenshot": state.get("ui_login_screenshot"),
            }
        )

    api_result = state.get("api_execution_result")
    if api_result:
        for result in api_result.get("results", []):
            if result.get("skipped"):
                continue
            if result.get("passed"):
                continue
            failures.append(
                {
                    "source": "api",
                    "label": result.get("label", ""),
                    "method": result.get("method", ""),
                    "url": result.get("url", ""),
                    "status_code": result.get("status_code", 0),
                    "envelope_status_code": result.get("envelope_status_code"),
                    "category": result.get("category", ""),
                    "failure_type": result.get("failure_type", "api_assertion"),
                    "failure_reason": result.get("failure_reason", ""),
                    "error": result.get("error", ""),
                    "body": str(result.get("body", ""))[:500] if result.get("body") else "",
                }
            )

    ui_result = state.get("ui_execution_result")
    if ui_result:
        failed_commands_by_case: dict[str, list[dict]] = {}
        for command in ui_result.get("commands", []):
            if command.get("status") == "skipped":
                continue
            if command.get("passed") is True or command.get("status_code", -1) == 0:
                continue
            key = str(command.get("case_index"))
            failed_commands_by_case.setdefault(key, []).append(command)

        for case in ui_result.get("cases", []):
            if case.get("passed"):
                continue
            case_key = str(case.get("case_index"))
            failed_commands = failed_commands_by_case.get(case_key, [])
            failures.append(
                {
                    "source": "ui_case",
                    "case_index": case.get("case_index"),
                    "title": case.get("title", "UI case"),
                    "screenshots": case.get("screenshots", []),
                    "failed_commands": failed_commands,
                }
            )

    return failures


def _collect_api_advisories(state: AgentState) -> list[dict]:
    api_result = state.get("api_execution_result") or {}
    advisories = []
    for result in api_result.get("results", []):
        if not result.get("advisory"):
            continue
        advisories.append(
            {
                "source": "api",
                "title": "API authentication policy advisory",
                "severity": "WARNING",
                "method": result.get("method", ""),
                "url": result.get("url", ""),
                "status_code": result.get("status_code"),
                "category": result.get("category", ""),
                "advisory_type": result.get("advisory_type", "api_advisory"),
                "description": result.get("warning") or result.get("skip_reason") or "API advisory finding.",
            }
        )
    return advisories


def _api_skip_note_counts(state: AgentState) -> dict[str, int]:
    api_result = state.get("api_execution_result") or {}
    results = api_result.get("results") or []
    budget_summary_count = int(api_result.get("budget_skipped") or 0)
    return {
        "environment_not_executable": sum(
            1 for result in results if result.get("skip_type") == "environment_not_executable"
        ),
        "execution_budget_exhausted": max(
            budget_summary_count,
            sum(
                1 for result in results if result.get("skip_type") == "execution_budget_exhausted"
            ),
        ),
    }


def _ui_command_action_text(command: dict | None) -> str:
    if not command:
        return "执行步骤"
    normalized = str(command.get("normalized_command") or command.get("command") or "")
    name = normalized.split(maxsplit=1)[0].lower() if normalized.split() else ""
    if name in {"open", "goto"}:
        return "打开或恢复页面"
    if name == "state-load":
        return "恢复浏览器状态"
    if name == "click":
        return "点击页面元素"
    if name in {"fill", "type"}:
        return "输入测试数据"
    if name == "select":
        return "选择下拉选项"
    if name == "reload":
        return "刷新页面"
    if name == "screenshot":
        return "保存截图证据"
    if name == "snapshot":
        return "读取页面结构"
    return "执行页面操作"


def _ui_error_text(command: dict | None) -> str:
    if not command:
        return "该用例未按预期完成。"
    stderr = str(command.get("stderr") or "").strip()
    status_code = command.get("status_code")
    if "timeout" in stderr.lower() or status_code == -1:
        return "页面操作等待超时，可能是页面加载较慢、跳转未完成，或目标页面没有按预期响应。"
    if "not found" in stderr.lower() or "does not match any elements" in stderr.lower():
        return "页面上没有找到目标元素，可能是页面结构变化、入口不可见，或当前业务状态不满足操作条件。"
    if "Screenshot file was not created" in stderr:
        return "截图文件没有成功生成，通常是前一步页面操作失败后导致证据保存中断。"
    if stderr:
        return f"执行时返回错误：{stderr[:180]}"
    return "该步骤返回失败状态。"


def _build_bug_findings(failure_details: list[dict]) -> list[dict]:
    bugs_found = []
    seen: set[str] = set()

    for failure in failure_details:
        source = failure.get("source", "unknown")
        if source == "api":
            label = failure.get("label", "")
            status_code = failure.get("status_code", 0)
            envelope_status_code = failure.get("envelope_status_code")
            failure_type = failure.get("failure_type", "api_assertion")
            method = failure.get("method", "")
            url = failure.get("url", "")
            key = f"api:{method}:{url}:{status_code}:{envelope_status_code}:{failure_type}:{label}"
            if key in seen:
                continue
            seen.add(key)
            if failure_type == "backend_validation_contract":
                title = f"Backend validation contract failure: {method} {url}"
                severity = "HIGH"
                detail_status = (
                    f"HTTP {status_code}, body code {envelope_status_code}"
                    if envelope_status_code is not None
                    else f"HTTP {status_code}"
                )
                description = (
                    f"Invalid-input request {label} returned {detail_status}. "
                    f"{failure.get('failure_reason') or 'Backend validation did not return the expected 4xx contract.'}"
                )
            elif status_code == 0:
                title = f"API connection failed: {method} {url}"
                severity = "HIGH"
                description = f"Request {label} could not reach the server: {failure.get('error', 'Unknown error')}"
            elif failure_type == "backend_error" or status_code >= 500:
                title = f"API server error: {method} {url}"
                severity = "CRITICAL"
                description = f"Request {label} returned {status_code}."
            elif failure_type == "auth_failure" or status_code in {401, 403} or envelope_status_code in {401, 403}:
                title = f"API authentication failed: {method} {url}"
                severity = "HIGH"
                description = f"Request {label} returned an unauthorized status."
            elif failure_type == "schema_contract":
                title = f"API schema contract failed: {method} {url}"
                severity = "MEDIUM"
                description = f"Request {label} returned JSON that does not match the documented schema."
            elif status_code >= 400:
                title = f"API client error: {method} {url}"
                severity = "MEDIUM"
                description = f"Request {label} returned {status_code}."
            else:
                title = f"API assertion failed: {method} {url}"
                severity = "MEDIUM"
                description = f"Request {label} did not satisfy its expected result."

            bugs_found.append(
                {
                    "title": title,
                    "severity": severity,
                    "description": description,
                    "source": "api",
                    "category": report_category_for_failure(failure_type),
                }
            )
        elif source == "ui_case":
            title = failure.get("title", "UI case")
            failed_commands = failure.get("failed_commands") or []
            primary_command = failed_commands[0] if failed_commands else None
            key = f"ui_case:{failure.get('case_index')}:{title}:{_ui_error_text(primary_command)}"
            if key in seen:
                continue
            seen.add(key)
            bugs_found.append(
                {
                    "title": f"UI 用例未通过：{title}",
                    "severity": "MEDIUM",
                    "description": (
                        f"用例执行到“{_ui_command_action_text(primary_command)}”时未能继续。"
                        f"{_ui_error_text(primary_command)}"
                    ),
                    "source": "ui",
                    "category": report_category_for_failure((primary_command or {}).get("failure_type") or "ui_command_failed"),
                    "screenshots": failure.get("screenshots", []),
                }
            )
        elif source == "setup":
            key = f"setup:{failure.get('reason')}"
            if key in seen:
                continue
            seen.add(key)
            bugs_found.append(
                {
                    "title": "UI pre-test setup verification failed",
                    "severity": "HIGH",
                    "description": failure.get("reason", "Pre-test setup could not be verified after running the setup flow."),
                    "source": "ui_setup",
                    "screenshot": failure.get("screenshot"),
                }
            )

    return bugs_found


def _build_recommendations(
    state: AgentState,
    failure_details: list[dict],
    api_counts: dict,
    ui_counts: dict,
) -> list[str]:
    recommendations = []
    setup_required = bool((state.get("setup_instructions") or state.get("login_instructions") or "").strip())
    login_verified = state.get("login_verified")
    login_reason = state.get("login_verification_reason") or state.get("last_error")
    setup_result = state.get("setup_result") or state.get("login_result") or {}

    if _has_api_target(state) and api_counts["total"] == 0:
        recommendations.append("没有执行 API 请求，请检查 API 文档、base URL 或测试类型配置是否完整。")
    if setup_required and setup_result.get("required") and login_verified is False:
        recommendations.append(
            f"前置准备没有通过校验，请核对账号、验证码、租户选择、环境入口等信息。当前原因：{_strip_terminal_punctuation(login_reason)}。"
        )
        recommendations.append("建议重新运行前先确认目标页面能人工完成相同准备流程，并保持测试环境可写/可读状态明确。")
    if any(
        f.get("status_code") in {401, 403} or f.get("envelope_status_code") in {401, 403}
        for f in failure_details
    ):
        recommendations.append("部分接口返回未授权，请为受保护接口补充 token 或请求头。")
    if any(f.get("failure_type") == "backend_validation_contract" for f in failure_details):
        recommendations.append("部分参数校验负向用例返回了成功 HTTP 状态或 5xx 业务错误，建议后端统一返回明确的 4xx 校验失败契约。")
    if any(f.get("status_code", 0) >= 500 for f in failure_details):
        recommendations.append("部分接口返回服务端错误，需要排查后端异常或测试数据状态。")
    skip_note_counts = _api_skip_note_counts(state)
    if skip_note_counts["environment_not_executable"]:
        recommendations.append("部分写入方法被当前环境或上游网关拒绝，建议确认测试环境是否开放 POST/PUT/PATCH/DELETE 后再执行写入覆盖。")
    if skip_note_counts["execution_budget_exhausted"]:
        recommendations.append("本次 API 文档规模超过执行预算，建议缩小接口范围或调整 API_MAX_EXECUTED_REQUESTS 后分批执行。")
    if any(
        "not found" in str(command.get("stderr", "")).lower()
        for failure in failure_details
        for command in failure.get("failed_commands", [])
    ):
        recommendations.append("部分 UI 元素没有被找到，请检查页面是否异步加载、权限菜单是否缺失，或选择器是否需要重新生成。")
    if ui_counts["total"] == 0 and (state.get("test_type") or "").lower() in {"ui", "full"} and not (setup_required and setup_result.get("required") and login_verified is False):
        recommendations.append("没有执行 UI 用例，请检查目标 URL 是否可访问，或前置准备是否让页面进入了可测试状态。")
    return recommendations


def _build_summary(api_counts: dict, ui_counts: dict, verdict: str, login_failed: bool, login_reason: str | None, failure_details: list[dict]) -> str:
    if login_failed:
        reason = _strip_terminal_punctuation(login_reason) or "未能验证准备结果"
        return (
            "本次运行在测试前置准备阶段失败，系统没有进入可继续测试的页面。"
            f"原因是：{reason}。"
            f"已执行 API 请求 {api_counts['executed']} 个，UI 用例 {ui_counts['executed']} 条。"
        )

    parts = []
    if api_counts["total"]:
        parts.append(
            f"API 测试执行 {api_counts['executed']} 个请求，"
            f"{api_counts['passed']} 个通过、{api_counts['failed']} 个失败、{api_counts['skipped']} 个跳过。"
        )
    else:
        parts.append("本次没有执行 API 请求。")

    if ui_counts["total"]:
        parts.append(
            f"UI 测试执行 {ui_counts['executed']} 条用例，"
            f"{ui_counts['passed']} 条通过、{ui_counts['failed']} 条失败，通过率 {ui_counts['pass_rate']}。"
        )
    else:
        parts.append("本次没有执行 UI 用例。")

    if verdict == "PASS":
        parts.append("所有已执行检查均通过，未发现需要关注的失败项。")
    elif verdict == "PARTIAL":
        failed_titles = [
            str(item.get("title"))
            for item in failure_details
            if item.get("source") == "ui_case" and item.get("title")
        ][:3]
        if failed_titles:
            parts.append(f"主要失败集中在：{'、'.join(failed_titles)}。")
        else:
            parts.append("部分测试通过，但仍存在失败项，需要结合缺陷列表继续排查。")
    elif verdict == "NOT_EXECUTED":
        parts.append("测试计划已生成，但没有产生可统计的执行结果。")
    else:
        parts.append("本次运行未通过，需要优先查看失败用例和建议项。")

    return "".join(parts)


def _agent_evaluation_recommendations(state: AgentState) -> list[str]:
    recommendations = []
    latest = state.get("evidence_evaluation")
    if not isinstance(latest, dict):
        return recommendations

    next_action = str(latest.get("next_action") or "")
    reason = _strip_terminal_punctuation(latest.get("reason"))
    if next_action in {"replan_api", "replan_ui"}:
        recommendations.append(f"智能体已因证据不足触发重规划：{reason}。")
    elif latest.get("sufficient_evidence") is False and reason:
        recommendations.append(f"本次证据仍不充分：{reason}。")

    for item in latest.get("diagnostics") or []:
        text = _strip_terminal_punctuation(item)
        if text:
            recommendations.append(text + "。")
    return recommendations[:4]


def _finding_evidence_ids(state: AgentState, finding: dict) -> list[str]:
    source = str(finding.get("source") or "")
    category = str(finding.get("category") or "")
    refs: list[str] = []
    seen: set[str] = set()
    for observation in state.get("agent_observations") or []:
        if not isinstance(observation, dict):
            continue
        if source == "api" and observation.get("layer") != "api":
            continue
        if source in {"ui", "ui_setup"} and observation.get("layer") != "ui":
            continue
        failure_type = str(observation.get("failure_type") or "")
        if category and report_category_for_failure(failure_type) != category:
            continue
        for evidence_id in observation.get("evidence_ids") or []:
            key = str(evidence_id)
            if key and key not in seen:
                seen.add(key)
                refs.append(key)
    return refs[:12]


async def _persist_run_findings(state: AgentState, findings: list[dict]) -> None:
    db = state.get("db_session")
    run_id = state.get("task_id")
    if not db or not run_id or not findings:
        return
    result = await db.execute(select(RunFinding.title).where(RunFinding.run_id == str(run_id)))
    existing_titles = {str(item) for item in result.scalars()}
    for finding in findings:
        title = str(finding.get("title") or "Runtime finding")[:255]
        if title in existing_titles:
            continue
        db.add(
            RunFinding(
                run_id=str(run_id),
                title=title,
                severity=str(finding.get("severity") or "MEDIUM").lower(),
                confidence=str(finding.get("confidence") or "medium").lower(),
                category=str(finding.get("category") or "unknown")[:64],
                surface=str(finding.get("source") or "")[:255] or None,
                description=str(finding.get("description") or title),
                evidence_ids_json=_finding_evidence_ids(state, finding),
                reproduction_steps_json=redact_sensitive_data(finding.get("reproduction_steps") or []),
                next_action=str(finding.get("next_action") or ""),
                status="open",
            )
        )
        existing_titles.add(title)


async def run(state: AgentState) -> AgentState:
    api_result = state.get("api_execution_result")
    ui_result = state.get("ui_execution_result")
    api_cases = state.get("api_cases") or []
    ui_cases = state.get("ui_cases") or []
    artifacts = state.get("artifacts") or {}
    tool_summary = summarize_tool_calls(state.get("tool_calls"))
    state["tool_summary"] = tool_summary

    failure_details = _collect_failure_details(state)
    advisory_findings = _collect_api_advisories(state)
    api_counts = _summary_counts(api_result)
    api_request_selection = (api_result or {}).get("request_selection") or {}
    ui_counts = _summary_counts(ui_result)
    total_executed = api_counts["executed"] + ui_counts["executed"]
    total_passed = api_counts["passed"] + ui_counts["passed"]
    total_failed = api_counts["failed"] + ui_counts["failed"]

    setup_required = bool((state.get("setup_instructions") or state.get("login_instructions") or "").strip())
    login_verified = state.get("login_verified")
    setup_result = state.get("setup_result") or state.get("login_result") or {}
    login_failed = setup_required and setup_result.get("required") and login_verified is False

    if total_executed == 0:
        verdict = "FAIL" if login_failed else "NOT_EXECUTED"
    elif total_failed == 0 and not login_failed:
        verdict = "PASS"
    elif total_passed > 0:
        verdict = "PARTIAL"
    else:
        verdict = "FAIL"

    login_reason = state.get("login_verification_reason") or state.get("last_error")
    test_type = (state.get("test_type") or "").lower()

    api_findings = []
    if api_counts["total"] > 0:
        api_findings.append(
            f"已执行 {api_counts['executed']} 个 API 请求，通过 {api_counts['passed']} 个，跳过 {api_counts['skipped']} 个。"
        )
        if api_request_selection.get("source") == ALL_SAFE_GET_COVERAGE_SOURCE:
            api_findings.append(
                "本次按 OpenAPI schema 确定性覆盖安全 GET/HEAD/OPTIONS："
                f"{api_request_selection.get('selected_safe_endpoint_total', api_counts['total'])}/"
                f"{api_request_selection.get('safe_endpoint_total', api_counts['total'])} 个安全端点已纳入执行选择。"
            )
    elif test_type == "ui":
        api_findings.append("本次为 UI 测试运行，API 测试不适用。")
    elif _has_api_target(state):
        api_findings.append("检测到 API 目标，但没有实际执行 API 请求。")
    else:
        api_findings.append("未提供 API Schema 或 base URL，因此未执行 API 测试。")

    ui_findings = []
    if ui_counts["total"] > 0:
        ui_findings.append(f"已执行 {ui_counts['total']} 条 UI 用例，通过 {ui_counts['passed']} 条。")
    elif login_failed:
        ui_findings.append(f"UI 前置准备失败，未进入后续探索：{login_reason}")
    elif test_type in {"ui", "full"}:
        ui_findings.append("已请求 UI 测试，但没有执行 UI 用例。")

    normalization_warnings = (ui_result or {}).get("normalization_warnings", [])
    execution_notes = []
    if normalization_warnings:
        execution_notes.append(
            f"执行前自动修正了 {len(normalization_warnings)} 条生成的 UI 命令，避免脚本语法问题影响结果。"
        )
    skip_note_counts = _api_skip_note_counts(state)
    if skip_note_counts["environment_not_executable"]:
        execution_notes.append(
            f"当前环境或上游网关拒绝了 {skip_note_counts['environment_not_executable']} 个写入方法请求，这些请求已标记为环境不可执行并从主通过率中排除。"
        )
    if skip_note_counts["execution_budget_exhausted"]:
        execution_notes.append(
            f"达到 API 请求执行预算后跳过了 {skip_note_counts['execution_budget_exhausted']} 个剩余请求，避免长时间运行触发 Worker 超时。"
        )
    if (
        api_request_selection.get("source") == ALL_SAFE_GET_COVERAGE_SOURCE
        and api_request_selection.get("omitted_safe_endpoint_total")
    ):
        execution_notes.append(
            "所有 GET/只读端点目标按执行预算有界覆盖，"
            f"另有 {api_request_selection.get('omitted_safe_endpoint_total')} 个安全端点未在本轮执行。"
        )
    if advisory_findings:
        execution_notes.append(
            f"记录了 {len(advisory_findings)} 个 API 鉴权策略提醒，未计入主通过率失败。"
        )

    summary = _build_summary(api_counts, ui_counts, verdict, login_failed, login_reason, failure_details)

    recommendations = _build_recommendations(
        state, failure_details, api_counts, ui_counts
    )
    for recommendation in _agent_evaluation_recommendations(state):
        if recommendation not in recommendations:
            recommendations.append(recommendation)

    final_report = {
        "title": "测试运行报告",
        "summary": summary,
        "api_test_summary": {
            **api_counts,
            "planned_cases": len(api_cases),
            "has_execution": api_counts["executed"] > 0,
            "request_selection": api_request_selection,
            "key_findings": api_findings,
        },
        "ui_test_summary": {
            **ui_counts,
            "planned_cases": len(ui_cases),
            "has_execution": ui_counts["executed"] > 0,
            "key_findings": ui_findings,
        },
        "bugs_found": _build_bug_findings(failure_details),
        "advisory_findings": advisory_findings,
        "recommendations": recommendations,
        "execution_notes": execution_notes,
        "agent_diagnostics": {
            "mission_plan": state.get("agent_mission_plan"),
            "roster": state.get("agent_roster", []),
            "delegation_trace": state.get("agent_delegation_trace", []),
            "react_trace": (state.get("agent_react_trace") or [])[-80:],
            "latest_evaluation": state.get("evidence_evaluation"),
            "evaluations": state.get("agent_evaluations", []),
            "attempt_history": state.get("agent_attempt_history", []),
            "replan_counts": state.get("agent_replan_counts", {}),
            "case_diagnostics": state.get("agent_case_diagnostics", []),
        },
        "tool_summary": tool_summary,
        "skill_plan": state.get("skill_plan", []),
        "overall_verdict": verdict,
        "artifacts": {
            "screenshots": artifacts.get("ui_screenshots", []),
            "ui_case_evidence": artifacts.get("ui_case_evidence", []),
            "ui_command_count": len(artifacts.get("ui_commands", [])),
            "api_result_count": len((api_result or {}).get("results", [])),
            "tool_call_count": tool_summary.get("total", 0),
        },
    }

    state["final_report"] = final_report
    record_tool_call(
        state,
        tool_name="reporter.failure_analysis",
        layer="reporter",
        status="success",
        input_summary={
            "api_results": len((api_result or {}).get("results", [])),
            "ui_cases": len((ui_result or {}).get("cases", [])),
            "tool_calls": tool_summary.get("total", 0),
        },
        output_summary={
            "verdict": verdict,
            "bugs_found": len(final_report["bugs_found"]),
            "recommendations": len(final_report["recommendations"]),
        },
        metadata={
            "reason": "Synthesize mission evidence, tool observations, findings, and reusable assets into the final report.",
            "next_decision": "complete_or_persist_memory",
        },
    )
    state["tool_summary"] = summarize_tool_calls(state.get("tool_calls"))
    final_report["tool_summary"] = state["tool_summary"]
    final_report["artifacts"]["tool_call_count"] = state["tool_summary"].get("total", 0)
    await _persist_run_findings(state, final_report["bugs_found"])
    artifacts["tool_calls"] = state.get("tool_calls", [])
    artifacts["tool_summary"] = state["tool_summary"]
    state["artifacts"] = artifacts

    detail = f"Report generated: {final_report['overall_verdict']}"
    state.setdefault("workflow_steps", []).append(
        {
            "node": "reporter",
            "status": "done",
            "detail": detail,
        }
    )
    await persist_progress(state, "reporter", "done", detail)
    return state

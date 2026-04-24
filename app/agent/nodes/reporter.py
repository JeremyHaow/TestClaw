import json
import logging

from langchain_core.messages import HumanMessage

from app.agent.prompts import REPORTER_PROMPT
from app.agent.state import AgentState
from app.core.llm_gateway import llm_gateway

logger = logging.getLogger(__name__)


def _summarize_api_results(api_result: dict | None) -> str:
    if not api_result:
        return "No API tests executed"
    total = api_result.get("total", 0)
    passed = api_result.get("passed", 0)
    failed = api_result.get("failed", 0)
    return f"Total: {total}, Passed: {passed}, Failed: {failed}"


def _summarize_ui_results(ui_result: dict | None) -> str:
    if not ui_result:
        return "No UI tests executed"
    total = ui_result.get("total", 0)
    passed = ui_result.get("passed", 0)
    failed = ui_result.get("failed", 0)
    return f"Total: {total}, Passed: {passed}, Failed: {failed}"


def _collect_failure_details(state: AgentState) -> list[dict]:
    """Collect all failure details from API and UI results."""
    failures = []

    # API failures
    api_result = state.get("api_execution_result")
    if api_result:
        for r in api_result.get("results", []):
            if not r.get("passed"):
                failures.append({
                    "source": "api",
                    "label": r.get("label", ""),
                    "method": r.get("method", ""),
                    "url": r.get("url", ""),
                    "status_code": r.get("status_code", 0),
                    "error": r.get("error", ""),
                    "body": str(r.get("body", ""))[:500] if r.get("body") else "",
                })

    # UI failures
    ui_result = state.get("ui_execution_result")
    if ui_result:
        for cmd in ui_result.get("commands", []):
            if cmd.get("status_code", -1) != 0:
                failures.append({
                    "source": "ui",
                    "command": cmd.get("command", ""),
                    "status_code": cmd.get("status_code", -1),
                    "stderr": cmd.get("stderr", "")[:500],
                })

    return failures


async def run(state: AgentState) -> AgentState:
    db = state.get("db_session")
    api_result = state.get("api_execution_result")
    ui_result = state.get("ui_execution_result")
    api_plan = state.get("api_plan")
    ui_plan = state.get("ui_plan")
    api_cases = state.get("api_cases") or []
    ui_cases = state.get("ui_cases") or []
    artifacts = state.get("artifacts") or {}

    failure_details = _collect_failure_details(state)

    # Try LLM-generated report
    llm_report = None
    if db:
        try:
            llm = await llm_gateway.get_planner(db)
            prompt = REPORTER_PROMPT.format(
                test_plan=json.dumps({"api_plan": api_plan, "ui_plan": ui_plan}, ensure_ascii=False, default=str),
                api_case_count=len(api_cases),
                ui_case_count=len(ui_cases),
                api_results_summary=_summarize_api_results(api_result),
                ui_results_summary=_summarize_ui_results(ui_result),
                failure_details=json.dumps(failure_details, ensure_ascii=False, default=str)[:2000],
            )
            resp = await llm.ainvoke([HumanMessage(content=prompt)])
            content = resp.content if hasattr(resp, "content") else str(resp)
            text = content.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            llm_report = json.loads(text)
        except Exception as e:
            logger.warning("Reporter LLM call failed: %s, using fallback", e)

    # Build report
    if llm_report and isinstance(llm_report, dict):
        final_report = llm_report
    else:
        # Fallback: build report from raw data
        api_total = (api_result or {}).get("total", 0)
        api_passed = (api_result or {}).get("passed", 0)
        ui_total = (ui_result or {}).get("total", 0)
        ui_passed = (ui_result or {}).get("passed", 0)

        all_passed = (
            (api_result or {}).get("all_passed", True)
            and (ui_result or {}).get("all_passed", True)
        )
        any_executed = api_total > 0 or ui_total > 0

        if all_passed and any_executed:
            verdict = "PASS"
        elif any_executed:
            verdict = "PARTIAL"
        else:
            verdict = "FAIL"

        final_report = {
            "title": "测试运行报告",
            "summary": f"API 测试: {api_passed}/{api_total} 通过, UI 测试: {ui_passed}/{ui_total} 通过",
            "api_test_summary": {
                "total": api_total,
                "passed": api_passed,
                "failed": api_total - api_passed,
                "pass_rate": f"{round(api_passed / api_total * 100, 1)}%" if api_total else "0%",
                "key_findings": [],
            },
            "ui_test_summary": {
                "total": ui_total,
                "passed": ui_passed,
                "failed": ui_total - ui_passed,
                "pass_rate": f"{round(ui_passed / ui_total * 100, 1)}%" if ui_total else "0%",
                "key_findings": [],
            },
            "bugs_found": [],
            "recommendations": [],
            "overall_verdict": verdict,
        }

    # Add artifact info
    final_report["artifacts"] = {
        "screenshots": artifacts.get("ui_screenshots", []),
        "ui_command_count": len(artifacts.get("ui_commands", [])),
        "api_result_count": api_total if api_result else 0,
    }

    state["final_report"] = final_report

    state.setdefault("workflow_steps", []).append(
        {
            "node": "reporter",
            "status": "done",
            "detail": f"Report generated: {final_report.get('overall_verdict', 'UNKNOWN')}",
        }
    )
    return state

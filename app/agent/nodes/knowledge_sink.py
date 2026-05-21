import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage

from app.agent.prompts import RCA_PROMPT
from app.agent.state import AgentState
from app.core.llm_gateway import llm_gateway

logger = logging.getLogger(__name__)


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


async def run(state: AgentState) -> AgentState:
    if not state.get("last_error"):
        state.setdefault("workflow_steps", []).append(
            {"node": "knowledge_sink", "status": "done", "detail": "Task completed successfully, knowledge stored"}
        )
        return state

    db = state.get("db_session")
    execution_result = state.get("execution_result") or {}
    stderr = execution_result.get("stderr", "")
    bug_report = None

    if db:
        try:
            llm = await llm_gateway.get_planner(db)
            prompt = RCA_PROMPT.format(
                stderr=stderr[:3000],
                network_logs="No network logs available",
            )
            resp = await llm.ainvoke([HumanMessage(content=prompt)])
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

    if bug_report is None:
        bug_report = {
            "title": "Automated test failure detected",
            "root_cause": state["last_error"],
            "reproduce_steps": "Run the generated script against the target environment.",
            "fix_suggestion": "Inspect the execution log and target application behavior.",
        }

    state["bug_report"] = bug_report

    if db:
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

    state.setdefault("workflow_steps", []).append(
        {"node": "knowledge_sink", "status": "done", "detail": "Bug report generated"}
    )

    # After persisting bug report, also store knowledge
    if db and bug_report:
        try:
            from app.models.knowledge import KnowledgeEntry
            knowledge = KnowledgeEntry(
                content=(
                    f"Bug: {_to_text(bug_report.get('title'), 'Automated test failure detected')}\n"
                    f"Root Cause: {_to_text(bug_report.get('root_cause'), 'Unknown root cause')}\n"
                    f"Fix: {_to_text(bug_report.get('fix_suggestion'), '')}"
                ),
                source_script_id=state.get("task_id"),
            )
            db.add(knowledge)
            await db.commit()
        except Exception as e:
            await db.rollback()
            logger.warning("Failed to persist knowledge: %s", e)

    return state

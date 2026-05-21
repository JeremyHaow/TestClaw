import json
import logging
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from langchain_core.messages import HumanMessage
from pydantic import BaseModel
from sqlalchemy import select

from app.agent.prompts import TC_GEN_PROMPT
from app.core.dependencies import CurrentUser, DbSession
from app.core.llm_gateway import llm_gateway
from app.models.test_case import TestCase, TestSuite
from app.schemas.test_case import TestCaseCreate, TestCaseRead

logger = logging.getLogger(__name__)
router = APIRouter()

_API_CATEGORY_LABELS = {
    "api",
    "http",
    "rest",
    "graphql",
    "smoke",
    "auth",
    "contract",
    "param_validation",
    "security",
}
_UI_CATEGORY_LABELS = {
    "ui",
    "browser",
    "e2e",
    "page_load",
    "visual",
    "navigation",
    "login",
}
_REQUEST_TEMPLATE_KEYS = {
    "method",
    "url",
    "path",
    "endpoint",
    "base_url",
    "headers",
    "body",
    "json",
    "query_params",
    "params",
    "expected_status",
}
_PLAYWRIGHT_COMMAND_PREFIXES = (
    "open ",
    "goto ",
    "click ",
    "fill ",
    "type ",
    "snapshot",
    "screenshot",
    "hover ",
    "press ",
    "wait ",
    "sleep ",
    "assert snapshot",
    "expect snapshot",
)


def _ensure_array(value) -> list[str]:
    """Convert value to array of strings if it's a string."""
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        # Split by numbered patterns like "1. xxx" or "1、xxx" or newlines
        parts = re.split(r'\d+[\.\、\)\]\s]+', value)
        # Filter empty strings and strip whitespace
        return [p.strip() for p in parts if p.strip()]
    return []


def _normalize_case(case: dict) -> dict:
    """Normalize a test case to ensure steps and expected are arrays."""
    case['steps'] = _ensure_array(case.get('steps', []))
    case['expected'] = _ensure_array(case.get('expected', []))
    return case


def _normalized_label(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _request_template_from_mapping(value: Any) -> dict:
    if not isinstance(value, dict):
        return {}
    nested = value.get("request_template")
    if isinstance(nested, dict):
        return dict(nested)
    request = value.get("request")
    if isinstance(request, dict):
        return _request_template_from_mapping(request)
    if any(key in value for key in _REQUEST_TEMPLATE_KEYS):
        return dict(value)
    return {}


def _extract_request_template(case: dict) -> dict:
    direct = _request_template_from_mapping(case)
    if direct:
        return direct

    test_data = case.get("test_data")
    from_test_data = _request_template_from_mapping(test_data)
    if from_test_data:
        return from_test_data

    for step in case.get("steps") or []:
        from_step = _request_template_from_mapping(step)
        if from_step:
            return from_step
    return {}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _extract_playwright_commands(case: dict) -> list[str]:
    for container in (case, case.get("test_data")):
        if not isinstance(container, dict):
            continue
        for key in ("playwright_commands", "commands"):
            commands = _string_list(container.get(key))
            if commands:
                return commands

    commands: list[str] = []
    for step in case.get("steps") or []:
        if isinstance(step, dict):
            for key in ("playwright_commands", "command"):
                commands.extend(_string_list(step.get(key)))
        elif isinstance(step, str):
            step_text = step.strip()
            if step_text.lower().startswith(_PLAYWRIGHT_COMMAND_PREFIXES):
                commands.append(step_text)
    return commands


def _suite_case_kind(case: dict) -> str:
    category = _normalized_label(case.get("category"))
    declared_type = _normalized_label(case.get("type") or case.get("case_type"))

    for label in (declared_type, category):
        if label == "api":
            return "api"
        if label == "ui":
            return "ui"

    if _extract_request_template(case):
        return "api"
    if _extract_playwright_commands(case):
        return "ui"

    for label in (declared_type, category):
        if label in _UI_CATEGORY_LABELS:
            return "ui"
        if label in _API_CATEGORY_LABELS:
            return "api"

    return "ui"


def _case_to_suite_payload(test_case: TestCase) -> dict:
    test_data = test_case.test_data or {}
    payload = {
        "title": test_case.title,
        "category": getattr(test_case, "category", "api") or "api",
        "priority": getattr(test_case, "priority", "P1") or "P1",
        "steps": test_case.steps or [],
        "expected": test_case.expected or [],
        "preconditions": test_case.preconditions or {},
        "test_data": test_data,
    }
    request_template = _extract_request_template(payload)
    if request_template:
        payload["request_template"] = request_template
    playwright_commands = _extract_playwright_commands(payload)
    if playwright_commands:
        payload["playwright_commands"] = playwright_commands
    payload["case_type"] = _suite_case_kind(payload)
    return payload


def _build_suite_case_payloads(cases: list[TestCase]) -> tuple[list[dict], list[dict]]:
    api_cases: list[dict] = []
    ui_cases: list[dict] = []
    for test_case in cases:
        payload = _case_to_suite_payload(test_case)
        if payload["case_type"] == "api":
            api_cases.append(payload)
        else:
            ui_cases.append(payload)
    return api_cases, ui_cases


def _first_open_command_url(commands: list[str]) -> str:
    for command in commands:
        parts = command.strip().split(maxsplit=1)
        if len(parts) == 2 and parts[0].lower() in {"open", "goto"}:
            return parts[1].strip().strip("'\"")
    return ""


def _extract_suite_ui_seed_url(ui_cases: list[dict]) -> str:
    for case in ui_cases:
        test_data = case.get("test_data") if isinstance(case.get("test_data"), dict) else {}
        for key in ("target_url", "url", "base_url"):
            value = str(test_data.get(key) or "").strip()
            if value.startswith(("http://", "https://")):
                return value
        command_url = _first_open_command_url(case.get("playwright_commands") or [])
        if command_url.startswith(("http://", "https://")):
            return command_url
    return ""


def _extract_suite_target_url(api_cases: list[dict], ui_cases: list[dict]) -> str:
    for case in api_cases:
        template = _extract_request_template(case)
        for key in ("base_url", "url"):
            value = str(template.get(key) or "").strip()
            if value.startswith(("http://", "https://")):
                return value

    ui_seed_url = _extract_suite_ui_seed_url(ui_cases)
    if ui_seed_url:
        return ui_seed_url

    return "suite"


def _suite_worker_kwargs(
    agent_test_type: str,
    api_cases: list[dict],
    ui_cases: list[dict],
) -> dict:
    kwargs = {
        "test_type": agent_test_type,
        "source_input": "suite",
        "api_cases": api_cases,
        "ui_cases": ui_cases,
    }
    ui_seed_url = _extract_suite_ui_seed_url(ui_cases)
    if ui_seed_url:
        kwargs["ui_seed_url"] = ui_seed_url
        kwargs["input_type"] = "url"
    return kwargs


@router.post("", response_model=TestCaseRead)
async def create_test_case(payload: TestCaseCreate, db: DbSession, _: CurrentUser):
    data = payload.model_dump()
    data["steps"] = _ensure_array(data.get("steps", []))
    data["expected"] = _ensure_array(data.get("expected", []))
    test_case = TestCase(**data)
    db.add(test_case)
    await db.commit()
    await db.refresh(test_case)
    return test_case


@router.post("/generate", response_model=TestCaseRead)
async def generate_test_case(payload: TestCaseCreate, db: DbSession, _: CurrentUser):
    test_case = TestCase(**payload.model_dump())
    db.add(test_case)
    await db.commit()
    await db.refresh(test_case)
    return test_case


class AIGenerateRequest(BaseModel):
    feature_description: str
    api_schema: str = "N/A"
    count: int = 5


@router.post("/generate-ai")
async def generate_test_cases_ai(payload: AIGenerateRequest, db: DbSession, _: CurrentUser):
    try:
        llm = await llm_gateway.get_planner(db)
    except RuntimeError:
        raise HTTPException(status_code=400, detail="No default planner provider configured")

    prompt = TC_GEN_PROMPT.format(
        feature_description=payload.feature_description,
        api_schema=payload.api_schema,
    )
    try:
        resp = await llm.ainvoke([HumanMessage(content=prompt)])
        content = resp.content if hasattr(resp, "content") else str(resp)
        text = content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        cases = json.loads(text)
        if not isinstance(cases, list):
            cases = [cases]
        # Normalize all cases to ensure steps and expected are arrays
        cases = [_normalize_case(c) for c in cases]
        return {"cases": cases[:payload.count], "raw_response": content}
    except json.JSONDecodeError:
        return {"cases": [], "raw_response": content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI generation failed: {e}")


@router.get("", response_model=list[TestCaseRead])
async def list_test_cases(
    db: DbSession,
    _: CurrentUser,
    priority: str | None = Query(default=None),
    category: str | None = Query(default=None),
    source: str | None = Query(default=None),
):
    stmt = select(TestCase)
    if priority:
        stmt = stmt.where(TestCase.priority == priority)
    if category:
        stmt = stmt.where(TestCase.category == category)
    if source:
        stmt = stmt.where(TestCase.source == source)
    result = await db.execute(stmt.order_by(TestCase.created_at.desc()))
    return list(result.scalars())


@router.put("/{test_case_id}", response_model=TestCaseRead)
async def update_test_case(test_case_id: str, payload: TestCaseCreate, db: DbSession, _: CurrentUser):
    test_case = await db.get(TestCase, test_case_id)
    if test_case is None:
        raise HTTPException(status_code=404, detail="Test case not found")
    for key, value in payload.model_dump().items():
        setattr(test_case, key, value)
    await db.commit()
    await db.refresh(test_case)
    return test_case


@router.delete("/{test_case_id}")
async def delete_test_case(test_case_id: str, db: DbSession, _: CurrentUser):
    test_case = await db.get(TestCase, test_case_id)
    if test_case is None:
        raise HTTPException(status_code=404, detail="Test case not found")
    await db.delete(test_case)
    await db.commit()
    return {"message": "deleted"}


class SuiteCreate(BaseModel):
    name: str
    test_case_ids: list[str] = []


@router.post("/suites")
async def create_suite(payload: SuiteCreate, db: DbSession, _: CurrentUser):
    suite = TestSuite(name=payload.name, test_case_ids=payload.test_case_ids)
    db.add(suite)
    await db.commit()
    await db.refresh(suite)
    return suite


@router.get("/suites")
async def list_suites(db: DbSession, _: CurrentUser):
    result = await db.execute(select(TestSuite).order_by(TestSuite.created_at.desc()))
    return list(result.scalars())


@router.get("/suites/{suite_id}")
async def get_suite(suite_id: str, db: DbSession, _: CurrentUser):
    suite = await db.get(TestSuite, suite_id)
    if suite is None:
        raise HTTPException(status_code=404, detail="Suite not found")
    return suite


class SuiteUpdate(BaseModel):
    name: str
    test_case_ids: list[str] = []


@router.put("/suites/{suite_id}")
async def update_suite(suite_id: str, payload: SuiteUpdate, db: DbSession, _: CurrentUser):
    suite = await db.get(TestSuite, suite_id)
    if suite is None:
        raise HTTPException(status_code=404, detail="Suite not found")
    suite.name = payload.name
    suite.test_case_ids = payload.test_case_ids
    await db.commit()
    await db.refresh(suite)
    return suite


@router.delete("/suites/{suite_id}")
async def delete_suite(suite_id: str, db: DbSession, _: CurrentUser):
    suite = await db.get(TestSuite, suite_id)
    if suite is None:
        raise HTTPException(status_code=404, detail="Suite not found")
    await db.delete(suite)
    await db.commit()
    return {"message": "deleted"}


@router.post("/suites/{suite_id}/run")
async def run_suite(suite_id: str, db: DbSession, _: CurrentUser):
    suite = await db.get(TestSuite, suite_id)
    if suite is None:
        raise HTTPException(status_code=404, detail="Suite not found")

    case_ids = suite.test_case_ids or []
    if not case_ids:
        raise HTTPException(status_code=400, detail="Suite has no test cases")

    cases = []
    for cid in case_ids:
        tc = await db.get(TestCase, cid)
        if tc:
            cases.append(tc)

    if not cases:
        raise HTTPException(status_code=400, detail="No valid test cases found in suite")

    api_cases, ui_cases = _build_suite_case_payloads(cases)
    has_api = bool(api_cases)
    has_ui = bool(ui_cases)

    from app.models.task import Task, TaskStatus as TS, TestType

    # Determine test type from normalized case kinds
    if has_api and has_ui:
        db_test_type = TestType.FULL
    elif has_ui:
        db_test_type = TestType.UI
    else:
        db_test_type = TestType.API

    target_url = _extract_suite_target_url(api_cases, ui_cases)

    task = Task(
        objective=f"执行测试套件: {suite.name}",
        target_url=target_url,
        test_type=db_test_type,
        status=TS.QUEUED,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    agent_test_type = "api" if has_api and not has_ui else "ui" if has_ui and not has_api else "auto"

    suite_kwargs = _suite_worker_kwargs(agent_test_type, api_cases, ui_cases)

    try:
        from app.worker.tasks import run_agent_task
        run_agent_task.delay(
            task.id,
            task.objective,
            target_url,
            **suite_kwargs,
        )
    except Exception as e:
        logger.warning("Celery dispatch failed for suite run: %s, running synchronously", e)
        from app.worker.tasks import run_graph_with_progress
        from app.agent.progress import determine_final_status, persist_task_state
        final_state = await run_graph_with_progress(
            {
                "task_id": task.id,
                "objective": task.objective,
                "target_url": target_url,
                "retry_count": 0,
                "messages": [],
                "workflow_steps": [],
                "db_session": db,
                **suite_kwargs,
            }
        )
        await persist_task_state(
            db, task, final_state,
            status=determine_final_status(final_state),
            refresh=True,
        )

    return {
        "suite_id": suite_id,
        "task_id": task.id,
        "status": "queued",
        "total": len(cases),
        "message": f"已提交 {len(cases)} 个测试用例执行",
    }

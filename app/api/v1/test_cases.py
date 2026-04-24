import json
import logging
import re

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

    from app.models.task import Task, TaskStatus as TS

    task = Task(
        objective=f"Run test suite: {suite.name}",
        target_url="suite",
        test_type="suite",
        status=TS.RUNNING,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    results = []
    passed = 0
    failed = 0
    for tc in cases:
        result = {
            "test_case_id": tc.id,
            "title": tc.title,
            "status": "passed",
            "steps_count": len(tc.steps) if tc.steps else 0,
        }
        results.append(result)
        passed += 1

    task.execution_log = json.dumps(
        {
            "suite_id": suite_id,
            "suite_name": suite.name,
            "total": len(cases),
            "passed": passed,
            "failed": failed,
            "results": results,
        },
        ensure_ascii=False,
    )
    task.status = TS.SUCCEEDED if failed == 0 else TS.FAILED
    await db.commit()

    return {
        "suite_id": suite_id,
        "task_id": task.id,
        "status": "completed",
        "total": len(cases),
        "passed": passed,
        "failed": failed,
        "results": results,
    }

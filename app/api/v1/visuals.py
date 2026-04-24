from fastapi import APIRouter
from sqlalchemy import select

from app.core.dependencies import CurrentUser, DbSession
from app.models.visual_baseline import VisualBaseline

router = APIRouter()


@router.get("")
async def list_visual_baselines(db: DbSession, _: CurrentUser):
    result = await db.execute(select(VisualBaseline).order_by(VisualBaseline.created_at.desc()))
    return list(result.scalars())


@router.post("/update")
async def update_visual_baseline(page_url: str, baseline_path: str, db: DbSession, _: CurrentUser):
    baseline = VisualBaseline(page_url=page_url, baseline_path=baseline_path)
    db.add(baseline)
    await db.commit()
    await db.refresh(baseline)
    return baseline

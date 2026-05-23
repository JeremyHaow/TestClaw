from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from app.core.dependencies import CurrentUser, DbSession
from app.services.knowledge_service import knowledge_service

router = APIRouter()

class KnowledgeCreate(BaseModel):
    content: str
    source_script_id: str | None = None


def _knowledge_payload(entry: Any) -> dict[str, Any]:
    return {
        "id": entry.id,
        "content": entry.content,
        "source_script_id": entry.source_script_id,
        "created_at": str(entry.created_at),
        "embedding_available": bool(entry.embedding),
    }


@router.post("", response_model=dict)
async def create_knowledge(payload: KnowledgeCreate, db: DbSession, _: CurrentUser):
    entry = await knowledge_service.create(db, content=payload.content, source_script_id=payload.source_script_id)
    return _knowledge_payload(entry)

@router.get("")
async def list_knowledge(db: DbSession, _: CurrentUser, limit: int = Query(default=50, ge=1, le=200)):
    entries = await knowledge_service.list(db, limit=limit)
    return [_knowledge_payload(e) for e in entries]

@router.get("/search")
async def search_knowledge(db: DbSession, _: CurrentUser, q: str = Query(...), limit: int = Query(default=10, ge=1, le=50)):
    entries = await knowledge_service.search(db, query=q, limit=limit)
    return [_knowledge_payload(e) for e in entries]

@router.get("/{entry_id}")
async def get_knowledge(entry_id: str, db: DbSession, _: CurrentUser):
    entry = await knowledge_service.get(db, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Knowledge entry not found")
    return _knowledge_payload(entry)

@router.delete("/{entry_id}")
async def delete_knowledge(entry_id: str, db: DbSession, _: CurrentUser):
    deleted = await knowledge_service.delete(db, entry_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Knowledge entry not found")
    return {"message": "deleted"}

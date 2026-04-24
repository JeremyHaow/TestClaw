from __future__ import annotations

import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.knowledge import KnowledgeEntry

logger = logging.getLogger(__name__)

class KnowledgeService:
    async def create(self, db: AsyncSession, content: str, source_script_id: str | None = None) -> KnowledgeEntry:
        entry = KnowledgeEntry(content=content, source_script_id=source_script_id)
        db.add(entry)
        await db.commit()
        await db.refresh(entry)
        return entry

    async def list(self, db: AsyncSession, limit: int = 50) -> list[KnowledgeEntry]:
        result = await db.execute(select(KnowledgeEntry).order_by(KnowledgeEntry.created_at.desc()).limit(limit))
        return list(result.scalars())

    async def get(self, db: AsyncSession, entry_id: str) -> KnowledgeEntry | None:
        return await db.get(KnowledgeEntry, entry_id)

    async def delete(self, db: AsyncSession, entry_id: str) -> bool:
        entry = await db.get(KnowledgeEntry, entry_id)
        if entry is None:
            return False
        await db.delete(entry)
        await db.commit()
        return True

    async def search(self, db: AsyncSession, query: str, limit: int = 10) -> list[KnowledgeEntry]:
        """Simple text search - searches content field for the query string."""
        stmt = select(KnowledgeEntry).where(
            KnowledgeEntry.content.ilike(f"%{query}%")
        ).order_by(KnowledgeEntry.created_at.desc()).limit(limit)
        result = await db.execute(stmt)
        return list(result.scalars())

knowledge_service = KnowledgeService()

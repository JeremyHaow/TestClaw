from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_document import ApiDocument
from app.tools.doc_parser import parse_api_document_content


class DocumentService:
    async def create(self, db: AsyncSession, name: str, raw_content: str, format: str) -> ApiDocument:
        parsed_endpoints = parse_api_document_content(raw_content, format)
        if not name or not name.strip():
            name = f"Document-{format}"
        document = ApiDocument(
            name=name,
            raw_content=raw_content,
            format=format,
            parsed_endpoints=parsed_endpoints,
        )
        db.add(document)
        await db.commit()
        await db.refresh(document)
        return document

    async def get(self, db: AsyncSession, document_id: str) -> ApiDocument | None:
        return await db.get(ApiDocument, document_id)

    async def list(self, db: AsyncSession) -> list[ApiDocument]:
        result = await db.execute(select(ApiDocument).order_by(ApiDocument.created_at.desc()))
        return list(result.scalars())

    async def update(self, db: AsyncSession, document_id: str, name: str) -> ApiDocument | None:
        document = await db.get(ApiDocument, document_id)
        if document is None:
            return None
        document.name = name
        await db.commit()
        await db.refresh(document)
        return document

    async def delete(self, db: AsyncSession, document_id: str) -> bool:
        document = await db.get(ApiDocument, document_id)
        if document is None:
            return False
        await db.delete(document)
        await db.commit()
        return True


doc_service = DocumentService()

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_document import ApiDocument
from app.tools.doc_parser import parse_api_document_content


class DocumentService:
    async def create(
        self,
        db: AsyncSession,
        name: str,
        raw_content: str,
        format: str,
        source_url: str | None = None,
    ) -> ApiDocument:
        parsed_endpoints = parse_api_document_content(raw_content, format)
        if not name or not name.strip():
            name = f"Document-{format}"
        document = ApiDocument(
            name=name,
            source_url=source_url,
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

    async def update(
        self,
        db: AsyncSession,
        document_id: str,
        name: str | None = None,
        raw_content: str | None = None,
        format: str | None = None,
        source_url: str | None = None,
    ) -> ApiDocument | None:
        document = await db.get(ApiDocument, document_id)
        if document is None:
            return None
        if name is not None:
            document.name = name
        if source_url is not None:
            document.source_url = source_url or None
        should_reparse = False
        if format is not None:
            document.format = format
            should_reparse = True
        if raw_content is not None:
            document.raw_content = raw_content
            should_reparse = True
        if should_reparse:
            document.parsed_endpoints = parse_api_document_content(document.raw_content, document.format)
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

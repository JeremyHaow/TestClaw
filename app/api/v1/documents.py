import httpx
from fastapi import APIRouter, HTTPException, UploadFile, File as FastAPIFile

from app.core.dependencies import CurrentUser, DbSession
from app.schemas.document import DocumentCreate, DocumentRead, DocumentUpdate
from app.services.doc_service import doc_service

router = APIRouter()


@router.post("/upload", response_model=DocumentRead)
async def upload_document(
    file: UploadFile = FastAPIFile(...),
    db: DbSession = None,
    _: CurrentUser = None,
):
    content = await file.read()
    raw_content = content.decode("utf-8", errors="replace")
    name = file.filename or "Uploaded document"
    fmt = "openapi"
    if name.endswith((".yaml", ".yml")):
        fmt = "yaml"
    elif name.endswith(".json"):
        fmt = "openapi"
    elif name.endswith((".postman_collection.json",)):
        fmt = "postman"
    return await doc_service.create(db, name, raw_content, fmt)


@router.post("/import", response_model=DocumentRead)
async def import_document(payload: DocumentCreate, db: DbSession, _: CurrentUser):
    raw_content = payload.raw_content
    name = payload.name

    if payload.url:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(payload.url)
                resp.raise_for_status()
                raw_content = resp.text
            if not name or name == "OpenAPI 文档":
                name = payload.url.split("/")[-1].split("?")[0] or "Imported document"
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to fetch URL: {e}")

    if not raw_content:
        raise HTTPException(status_code=400, detail="Either raw_content or url must be provided")

    return await doc_service.create(db, name, raw_content, payload.format)


@router.get("", response_model=list[DocumentRead])
async def list_documents(db: DbSession, _: CurrentUser):
    return await doc_service.list(db)


@router.get("/{document_id}", response_model=DocumentRead)
async def get_document(document_id: str, db: DbSession, _: CurrentUser):
    document = await doc_service.get(db, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


@router.put("/{document_id}", response_model=DocumentRead)
async def update_document(document_id: str, payload: DocumentUpdate, db: DbSession, _: CurrentUser):
    document = await doc_service.update(db, document_id, payload.name)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


@router.delete("/{document_id}")
async def delete_document(document_id: str, db: DbSession, _: CurrentUser):
    deleted = await doc_service.delete(db, document_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"message": "Document deleted"}

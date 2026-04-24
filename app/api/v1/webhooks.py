from fastapi import APIRouter

router = APIRouter()


@router.post("/ci")
async def ci_webhook(payload: dict):
    return {"status": "accepted", "payload": payload}

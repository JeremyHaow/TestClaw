import httpx
from fastapi import APIRouter

from app.core.dependencies import CurrentUser
from app.schemas.discovery import DiscoverModelsRequest, ModelItem

router = APIRouter()


@router.post("/discover-models", response_model=list[ModelItem])
async def discover_models(payload: DiscoverModelsRequest, _: CurrentUser):
    if payload.type == "openai":
        return await _discover_openai(payload.api_key, payload.base_url)
    if payload.type == "anthropic":
        return await _discover_anthropic(payload.api_key, payload.base_url)
    return []


async def _discover_openai(api_key: str, base_url: str | None) -> list[ModelItem]:
    url = (base_url or "https://api.openai.com/v1").rstrip("/") + "/models"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {api_key}"})
            resp.raise_for_status()
            data = resp.json().get("data", [])
            items = []
            for m in data:
                mid = m.get("id", "")
                if not mid:
                    continue
                items.append(ModelItem(id=mid, display_name=m.get("name") or mid))
            items.sort(key=lambda x: x.id)
            return items[:200]
    except Exception:
        return []


async def _discover_anthropic(api_key: str, base_url: str | None) -> list[ModelItem]:
    url = (base_url or "https://api.anthropic.com").rstrip("/") + "/v1/models"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                url,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
            )
            resp.raise_for_status()
            body = resp.json()
            data = body.get("data", [])
            items = []
            for m in data:
                mid = m.get("id", "")
                if not mid:
                    continue
                items.append(ModelItem(id=mid, display_name=m.get("display_name") or mid))
            items.sort(key=lambda x: x.id)
            return items[:200]
    except Exception:
        return []

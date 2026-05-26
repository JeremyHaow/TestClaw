import time

from fastapi import APIRouter, HTTPException
from langchain_core.messages import HumanMessage
from pydantic import BaseModel
from sqlalchemy import select, update

from app.core.dependencies import CurrentUser, DbSession
from app.core.redaction import redact_sensitive_text
from app.core.security import decrypt_value, encrypt_value, mask_secret
from app.models.llm_provider import LLMProvider, ProviderType
from app.schemas.provider import ProviderCreate, ProviderRead, ProviderUpdate

router = APIRouter()

_DEFAULT_ROLE_FIELDS = ("is_default_coder", "is_default_vision", "is_default_planner")


def _mask_api_key(encrypted: str) -> str | None:
    try:
        return mask_secret(decrypt_value(encrypted))
    except Exception:
        return mask_secret(encrypted)


def _to_schema(provider: LLMProvider) -> ProviderRead:
    return ProviderRead.model_validate(
        {
            **provider.__dict__,
            "type": provider.type.value,
            "api_key_masked": _mask_api_key(provider.api_key_encrypted),
        }
    )


async def _clear_conflicting_defaults(
    db: DbSession,
    payload: ProviderCreate | ProviderUpdate,
    *,
    exclude_id: str | None = None,
) -> None:
    for field in _DEFAULT_ROLE_FIELDS:
        if not getattr(payload, field):
            continue
        stmt = update(LLMProvider).values(**{field: False})
        if exclude_id is not None:
            stmt = stmt.where(LLMProvider.id != exclude_id)
        await db.execute(stmt)


@router.post("", response_model=ProviderRead)
async def create_provider(payload: ProviderCreate, db: DbSession, _: CurrentUser):
    await _clear_conflicting_defaults(db, payload)
    provider = LLMProvider(
        name=payload.name,
        type=ProviderType(payload.type),
        api_key_encrypted=encrypt_value(payload.api_key),
        model_name=payload.model_name,
        base_url=payload.base_url,
        is_default_coder=payload.is_default_coder,
        is_default_vision=payload.is_default_vision,
        is_default_planner=payload.is_default_planner,
        max_tokens=payload.max_tokens,
        temperature=payload.temperature,
        system_prompt=payload.system_prompt,
        agent_type=payload.agent_type,
    )
    db.add(provider)
    await db.commit()
    await db.refresh(provider)
    return _to_schema(provider)


@router.get("", response_model=list[ProviderRead])
async def list_providers(db: DbSession, _: CurrentUser):
    result = await db.execute(select(LLMProvider).order_by(LLMProvider.created_at.desc()))
    return [_to_schema(item) for item in result.scalars()]


@router.put("/{provider_id}", response_model=ProviderRead)
async def update_provider(provider_id: str, payload: ProviderUpdate, db: DbSession, _: CurrentUser):
    provider = await db.get(LLMProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    await _clear_conflicting_defaults(db, payload, exclude_id=provider_id)
    provider.name = payload.name
    provider.type = ProviderType(payload.type)
    if payload.api_key:
        provider.api_key_encrypted = encrypt_value(payload.api_key)
    provider.model_name = payload.model_name
    provider.base_url = payload.base_url
    provider.is_default_coder = payload.is_default_coder
    provider.is_default_vision = payload.is_default_vision
    provider.is_default_planner = payload.is_default_planner
    provider.max_tokens = payload.max_tokens
    provider.temperature = payload.temperature
    provider.system_prompt = payload.system_prompt
    provider.agent_type = payload.agent_type
    await db.commit()
    await db.refresh(provider)
    return _to_schema(provider)


@router.delete("/{provider_id}")
async def delete_provider(provider_id: str, db: DbSession, _: CurrentUser):
    provider = await db.get(LLMProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    await db.delete(provider)
    await db.commit()
    return {"message": "deleted"}


@router.post("/{provider_id}/test")
async def test_provider(provider_id: str, db: DbSession, _: CurrentUser):
    provider = await db.get(LLMProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")

    try:
        api_key = decrypt_value(provider.api_key_encrypted)
    except Exception:
        api_key = provider.api_key_encrypted

    start = time.time()
    try:
        if provider.type == ProviderType.OPENAI:
            from langchain_openai import ChatOpenAI
            llm = ChatOpenAI(
                model=provider.model_name,
                api_key=api_key,
                base_url=provider.base_url or None,
                max_tokens=50,
                timeout=30,
            )
        else:
            from langchain_anthropic import ChatAnthropic
            llm = ChatAnthropic(
                model=provider.model_name,
                api_key=api_key,
                base_url=provider.base_url or None,
                max_tokens=50,
                timeout=30,
            )

        resp = await llm.ainvoke([HumanMessage(content="Say 'ok' in one word.")])
        latency = int((time.time() - start) * 1000)
        return {
            "provider_id": provider_id,
            "status": "ok",
            "latency_ms": latency,
            "model_response": redact_sensitive_text(str(resp.content))[:100],
        }
    except Exception as e:
        latency = int((time.time() - start) * 1000)
        return {
            "provider_id": provider_id,
            "status": "error",
            "latency_ms": latency,
            "detail": redact_sensitive_text(str(e))[:200],
        }


@router.put("/{provider_id}/set-default", response_model=ProviderRead)
async def set_default_provider(provider_id: str, role: str, db: DbSession, _: CurrentUser):
    provider = await db.get(LLMProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    if role == "coder":
        await db.execute(update(LLMProvider).values(is_default_coder=False))
        provider.is_default_coder = True
    elif role == "vision":
        await db.execute(update(LLMProvider).values(is_default_vision=False))
        provider.is_default_vision = True
    elif role == "planner":
        await db.execute(update(LLMProvider).values(is_default_planner=False))
        provider.is_default_planner = True
    else:
        raise HTTPException(status_code=400, detail="Unsupported role")
    await db.commit()
    await db.refresh(provider)
    return _to_schema(provider)


class ProviderConfigUpdate(BaseModel):
    system_prompt: str | None = None
    agent_type: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None


@router.put("/{provider_id}/config", response_model=ProviderRead)
async def update_provider_config(provider_id: str, payload: ProviderConfigUpdate, db: DbSession, _: CurrentUser):
    provider = await db.get(LLMProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    if payload.system_prompt is not None:
        provider.system_prompt = payload.system_prompt
    if payload.agent_type is not None:
        provider.agent_type = payload.agent_type
    if payload.temperature is not None:
        provider.temperature = payload.temperature
    if payload.max_tokens is not None:
        provider.max_tokens = payload.max_tokens
    await db.commit()
    await db.refresh(provider)
    return _to_schema(provider)

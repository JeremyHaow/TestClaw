from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from app.config import settings
from app.core.security import decrypt_value
from app.models.llm_provider import LLMProvider, ProviderType


class LLMGateway:
    def build_client(self, provider: LLMProvider) -> BaseChatModel:
        key = decrypt_value(provider.api_key_encrypted)
        if provider.type == ProviderType.OPENAI:
            return ChatOpenAI(
                model=provider.model_name,
                api_key=key,
                base_url=provider.base_url or None,
                temperature=provider.temperature,
                max_tokens=provider.max_tokens,
                default_headers={"User-Agent": "TestClaw/1.0"},
            )
        if provider.type == ProviderType.ANTHROPIC:
            return ChatAnthropic(
                model=provider.model_name,
                api_key=key,
                temperature=provider.temperature,
                max_tokens=provider.max_tokens,
            )
        raise ValueError(f"Unsupported provider type: {provider.type}")

    async def _get_default(self, db: AsyncSession, field: str) -> BaseChatModel:
        stmt = select(LLMProvider).where(getattr(LLMProvider, field).is_(True), LLMProvider.is_active.is_(True))
        provider = (await db.execute(stmt)).scalar_one_or_none()
        if provider is None:
            raise RuntimeError(f"No active default provider for role: {field}")
        return self.build_client(provider)

    async def get_coder(self, db: AsyncSession) -> BaseChatModel:
        return await self._get_default(db, "is_default_coder")

    async def get_vision(self, db: AsyncSession) -> BaseChatModel:
        return await self._get_default(db, "is_default_vision")

    async def get_planner(self, db: AsyncSession) -> BaseChatModel:
        return await self._get_default(db, "is_default_planner")

    def build_fallback_openai_client(self, api_key: str | None = None) -> ChatOpenAI:
        return ChatOpenAI(
            model=settings.DEFAULT_MODEL_CODER,
            api_key=api_key or settings.DEFAULT_OPENAI_API_KEY,
            base_url=settings.DEFAULT_OPENAI_BASE_URL,
            temperature=0.2,
        )


llm_gateway = LLMGateway()

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from langchain_anthropic import ChatAnthropic
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

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

    def build_embeddings_client(self, provider: LLMProvider) -> Embeddings:
        key = decrypt_value(provider.api_key_encrypted)
        if provider.type != ProviderType.OPENAI:
            raise ValueError(f"Provider type does not support embeddings: {provider.type}")
        return OpenAIEmbeddings(
            model=settings.DEFAULT_EMBEDDING_MODEL,
            api_key=key,
            base_url=provider.base_url or None,
            default_headers={"User-Agent": "TestClaw/1.0"},
        )

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

    async def get_embeddings(self, db: AsyncSession) -> Embeddings:
        stmt = (
            select(LLMProvider)
            .where(LLMProvider.type == ProviderType.OPENAI, LLMProvider.is_active.is_(True))
            .order_by(LLMProvider.is_default_planner.desc(), LLMProvider.created_at.desc())
        )
        provider = (await db.execute(stmt)).scalars().first()
        if provider is not None:
            return self.build_embeddings_client(provider)
        if settings.DEFAULT_OPENAI_API_KEY:
            return self.build_fallback_openai_embeddings_client()
        raise RuntimeError("No active OpenAI-compatible embedding provider configured")

    def build_fallback_openai_client(self, api_key: str | None = None) -> ChatOpenAI:
        return ChatOpenAI(
            model=settings.DEFAULT_MODEL_CODER,
            api_key=api_key or settings.DEFAULT_OPENAI_API_KEY,
            base_url=settings.DEFAULT_OPENAI_BASE_URL,
            temperature=0.2,
        )

    def build_fallback_openai_embeddings_client(self, api_key: str | None = None) -> Embeddings:
        return OpenAIEmbeddings(
            model=settings.DEFAULT_EMBEDDING_MODEL,
            api_key=api_key or settings.DEFAULT_OPENAI_API_KEY,
            base_url=settings.DEFAULT_OPENAI_BASE_URL,
            default_headers={"User-Agent": "TestClaw/1.0"},
        )


llm_gateway = LLMGateway()

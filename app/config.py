import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "TestClaw"
    API_PREFIX: str = "/api/v1"
    DATABASE_URL: str = "sqlite+aiosqlite:///./testclaw.db"
    REDIS_URL: str = "redis://localhost:6379/0"
    FERNET_KEY: str = ""
    SECRET_KEY: str = "change-me"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    DEFAULT_ADMIN_USERNAME: str = "admin"
    DEFAULT_ADMIN_PASSWORD: str = "testclaw123"
    BACKEND_CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"
    LANGCHAIN_TRACING_V2: bool = False
    LANGCHAIN_API_KEY: str = ""
    LANGCHAIN_PROJECT: str = "testclaw"
    DEFAULT_OPENAI_API_KEY: str = ""
    DEFAULT_OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    DEFAULT_MODEL_CODER: str = "gpt-4o"
    DEFAULT_MODEL_VISION: str = "gpt-4o"
    DEFAULT_MODEL_PLANNER: str = "gpt-4o"
    DEFAULT_EMBEDDING_MODEL: str = "text-embedding-3-small"
    RAG_VECTOR_STORE_BACKEND: str = "database"
    MILVUS_URI: str = ""
    MILVUS_TOKEN: str = ""
    MILVUS_COLLECTION: str = "testclaw_knowledge"
    MILVUS_DIMENSION: int = 384
    SANDBOX_TIMEOUT: int = 120
    MAX_RETRY_COUNT: int = 3
    AGENT_MAX_REPLAN_ATTEMPTS: int = 2
    AGENT_TASK_SOFT_TIME_LIMIT_SECONDS: int = 2700
    AGENT_TASK_TIME_LIMIT_SECONDS: int = 3000
    API_MAX_EXECUTED_REQUESTS: int = 120
    API_REQUEST_TIMEOUT_SECONDS: float = 30.0
    API_REQUEST_RETRY_COUNT: int = 0
    PREFLIGHT_WORKER_TIMEOUT_SECONDS: float = 0.5
    PLAYWRIGHT_CLI_TIMEOUT_SECONDS: int = 30
    PLAYWRIGHT_SMART_WAIT_MS: int = 1500
    OSS_ENABLED: bool = False
    OSS_BUCKET: str = ""
    OSS_REGION: str = ""
    OSS_ENDPOINT: str = ""
    OSS_PUBLIC_BASE_URL: str = ""
    OSS_PREFIX: str = "testclaw/screenshots"
    OSS_USE_CNAME: bool = False
    PROJECT_ROOT: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[1])

    @field_validator("FERNET_KEY", mode="after")
    @classmethod
    def allow_empty_fernet_for_bootstrap(cls, value: str) -> str:
        return value.strip()

    @property
    def sandbox_dir(self) -> Path:
        return self.PROJECT_ROOT / "sandbox"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


def _configure_optional_langchain_tracing() -> None:
    if settings.LANGCHAIN_TRACING_V2 and not settings.LANGCHAIN_API_KEY.strip():
        settings.LANGCHAIN_TRACING_V2 = False
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
        os.environ["LANGSMITH_TRACING"] = "false"


_configure_optional_langchain_tracing()

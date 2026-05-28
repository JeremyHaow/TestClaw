from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.engine import make_url

from app.api.router import router as api_router
from app.config import settings
from app.database import AsyncSessionLocal, Base, engine
from app.services.auth_service import auth_service


def _should_create_all_on_startup(database_url: str) -> bool:
    return make_url(database_url).drivername.startswith("sqlite")


@asynccontextmanager
async def lifespan(_: FastAPI):
    if _should_create_all_on_startup(settings.DATABASE_URL):
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as session:
        await auth_service.ensure_default_admin(
            session,
            username=settings.DEFAULT_ADMIN_USERNAME,
            password=settings.DEFAULT_ADMIN_PASSWORD,
        )
    yield


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[item.strip() for item in settings.BACKEND_CORS_ORIGINS.split(",") if item.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Total-Count"],
)
app.include_router(api_router, prefix=settings.API_PREFIX)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

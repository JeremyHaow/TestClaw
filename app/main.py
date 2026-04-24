from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import router as api_router
from app.config import settings
from app.database import AsyncSessionLocal, Base, engine
from app.services.auth_service import auth_service


@asynccontextmanager
async def lifespan(_: FastAPI):
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
)
app.include_router(api_router, prefix=settings.API_PREFIX)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

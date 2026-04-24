from fastapi import APIRouter, HTTPException, status

from app.core.dependencies import CurrentUser, DbSession
from app.schemas.auth import LoginRequest, TokenResponse, UserRead
from app.services.auth_service import auth_service

router = APIRouter()


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: DbSession):
    user = await auth_service.authenticate(db, payload.username, payload.password)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")
    return TokenResponse(access_token=auth_service.issue_token(user))


@router.get("/me", response_model=UserRead)
async def me(current_user: CurrentUser):
    return current_user

from pydantic import BaseModel

from app.schemas.common import ORMModel


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserRead(ORMModel):
    id: str
    username: str
    is_active: bool
    is_admin: bool

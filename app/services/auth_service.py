from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import User


class AuthService:
    async def authenticate(self, db: AsyncSession, username: str, password: str) -> User | None:
        result = await db.execute(select(User).where(User.username == username, User.is_active.is_(True)))
        user = result.scalar_one_or_none()
        if user is None:
            return None
        if not verify_password(password, user.hashed_password):
            return None
        return user

    async def ensure_default_admin(self, db: AsyncSession, username: str, password: str) -> User:
        result = await db.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()
        if user is not None:
            return user
        user = User(username=username, hashed_password=hash_password(password), is_active=True, is_admin=True)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user

    def issue_token(self, user: User) -> str:
        return create_access_token(user.username)


auth_service = AuthService()

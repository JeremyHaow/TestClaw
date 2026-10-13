from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import User


class AdminPasswordResetError(ValueError):
    """An operator-requested password reset cannot safely be applied."""


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

    async def reset_admin_password(
        self, db: AsyncSession, username: str, password: str
    ) -> None:
        """Reset an existing active admin; caller must have trusted server access."""
        if not password.strip() or not 12 <= len(password) <= 1024:
            raise AdminPasswordResetError(
                "Password must contain 12 to 1024 characters and cannot be whitespace-only."
            )

        result = await db.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()
        if user is None:
            raise AdminPasswordResetError("User does not exist; no account was created.")
        if not user.is_admin:
            raise AdminPasswordResetError("User is not an administrator.")
        if not user.is_active:
            raise AdminPasswordResetError("Administrator is inactive; reset cannot enable an account.")

        user.hashed_password = hash_password(password)
        await db.commit()

    def issue_token(self, user: User) -> str:
        return create_access_token(user.username)


auth_service = AuthService()

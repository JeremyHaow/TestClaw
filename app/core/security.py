import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from jose import JWTError, jwt

from app.config import settings

ALGORITHM = "HS256"
PBKDF2_ITERATIONS = 600_000


def _fernet() -> Fernet:
    if not settings.FERNET_KEY:
        raise RuntimeError("FERNET_KEY is required for encryption")
    return Fernet(settings.FERNET_KEY.encode())


def encrypt_value(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_value(encrypted: str) -> str:
    try:
        return _fernet().decrypt(encrypted.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Invalid encrypted value") from exc


def mask_secret(value: str | None, keep: int = 4) -> str | None:
    if value is None:
        return None
    if len(value) <= keep:
        return "*" * len(value)
    return f"{'*' * max(len(value) - keep, 0)}{value[-keep:]}"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    ).hex()
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest}"


def verify_password(password: str, hashed_password: str) -> bool:
    try:
        scheme, iterations, salt, digest = hashed_password.split("$", 3)
    except ValueError:
        return False
    if scheme != "pbkdf2_sha256":
        return False
    computed = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        int(iterations),
    ).hex()
    return hmac.compare_digest(computed, digest)


def create_access_token(subject: str, expires_delta: timedelta | None = None) -> str:
    expire = datetime.now(UTC) + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    return jwt.encode({"sub": subject, "exp": expire}, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as exc:
        raise ValueError("Invalid access token") from exc

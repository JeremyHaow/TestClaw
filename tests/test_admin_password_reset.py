import asyncio
import getpass
import warnings
from collections.abc import AsyncGenerator, Iterator
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.api.v1.auth import router as auth_router
from app.cli import reset_admin_password as command
from app.config import settings
from app.core.security import PBKDF2_ITERATIONS, hash_password, verify_password
from app.database import get_db
from app.models.user import User
from app.services.auth_service import AdminPasswordResetError, auth_service

OLD_PASSWORD = "old-admin-password"
NEW_PASSWORD = "new-admin-password"


@pytest.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(User.__table__.create)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


def test_password_hash_uses_random_salt_and_rejects_wrong_password() -> None:
    first = hash_password(NEW_PASSWORD)
    second = hash_password(NEW_PASSWORD)

    assert first != second
    assert NEW_PASSWORD not in first
    scheme, iterations, salt, digest = first.split("$")
    assert scheme == "pbkdf2_sha256"
    assert int(iterations) == PBKDF2_ITERATIONS
    assert len(salt) == 32
    assert len(digest) == 64
    assert verify_password(NEW_PASSWORD, first)
    assert verify_password(NEW_PASSWORD, second)
    assert not verify_password(OLD_PASSWORD, first)


async def test_bootstrap_creates_admin_but_preserves_existing_password(db: AsyncSession) -> None:
    created = await auth_service.ensure_default_admin(db, "admin", OLD_PASSWORD)
    original_hash = created.hashed_password
    assert created.is_admin and created.is_active
    assert verify_password(OLD_PASSWORD, original_hash)

    existing = await auth_service.ensure_default_admin(db, "admin", NEW_PASSWORD)

    assert existing.id == created.id
    assert existing.hashed_password == original_hash
    assert await auth_service.authenticate(db, "admin", OLD_PASSWORD) is not None
    assert await auth_service.authenticate(db, "admin", NEW_PASSWORD) is None


async def test_reset_persists_hash_and_bootstrap_does_not_restore_old_password(
    db: AsyncSession,
) -> None:
    admin = await auth_service.ensure_default_admin(db, "admin", OLD_PASSWORD)
    before = (admin.id, admin.username, admin.created_at, admin.is_admin, admin.is_active)
    old_hash = admin.hashed_password
    other = await auth_service.ensure_default_admin(db, "other-admin", OLD_PASSWORD)
    other_hash = other.hashed_password

    await auth_service.reset_admin_password(db, "admin", NEW_PASSWORD)
    await db.refresh(admin)
    await db.refresh(other)

    assert admin.hashed_password != old_hash
    assert NEW_PASSWORD not in admin.hashed_password
    assert verify_password(NEW_PASSWORD, admin.hashed_password)
    assert not verify_password(OLD_PASSWORD, admin.hashed_password)
    assert (admin.id, admin.username, admin.created_at, admin.is_admin, admin.is_active) == before
    assert other.hashed_password == other_hash

    await auth_service.ensure_default_admin(db, "admin", OLD_PASSWORD)
    assert await auth_service.authenticate(db, "admin", NEW_PASSWORD) is not None
    assert await auth_service.authenticate(db, "admin", OLD_PASSWORD) is None


async def test_reset_missing_user_does_not_create_an_account(db: AsyncSession) -> None:
    with pytest.raises(AdminPasswordResetError, match="does not exist"):
        await auth_service.reset_admin_password(db, "missing", NEW_PASSWORD)
    assert (await db.scalars(select(User))).all() == []


@pytest.mark.parametrize("is_admin,is_active", [(False, True), (True, False)])
async def test_reset_refuses_non_admin_or_inactive_user_without_changing_flags(
    db: AsyncSession, is_admin: bool, is_active: bool
) -> None:
    user = User(
        username="restricted",
        hashed_password=hash_password(OLD_PASSWORD),
        is_admin=is_admin,
        is_active=is_active,
    )
    db.add(user)
    await db.commit()
    old_hash = user.hashed_password

    with pytest.raises(AdminPasswordResetError):
        await auth_service.reset_admin_password(db, user.username, NEW_PASSWORD)
    await auth_service.ensure_default_admin(db, user.username, NEW_PASSWORD)
    await db.refresh(user)

    assert user.hashed_password == old_hash
    assert (user.is_admin, user.is_active) == (is_admin, is_active)


@pytest.mark.parametrize("password", ["", " " * 12, "x" * 11, "x" * 1025])
async def test_reset_rejects_invalid_password_without_writing(
    db: AsyncSession, password: str
) -> None:
    admin = await auth_service.ensure_default_admin(db, "admin", OLD_PASSWORD)
    old_hash = admin.hashed_password
    with pytest.raises(AdminPasswordResetError, match="12 to 1024"):
        await auth_service.reset_admin_password(db, "admin", password)
    await db.refresh(admin)
    assert admin.hashed_password == old_hash


@pytest.mark.parametrize("password", ["x" * 12, "x" * 1024, "  密码 with spaces  "])
async def test_reset_preserves_valid_password_exactly(db: AsyncSession, password: str) -> None:
    await auth_service.ensure_default_admin(db, "admin", OLD_PASSWORD)
    await auth_service.reset_admin_password(db, "admin", password)
    assert await auth_service.authenticate(db, "admin", password) is not None
    if password != password.strip():
        assert await auth_service.authenticate(db, "admin", password.strip()) is None


@pytest.fixture
def terminal(monkeypatch: pytest.MonkeyPatch) -> Mock:
    monkeypatch.setattr(command.sys.stdin, "isatty", lambda: True)
    prompts = Mock(side_effect=[NEW_PASSWORD, NEW_PASSWORD])
    monkeypatch.setattr(command.getpass, "getpass", prompts)
    return prompts


def test_cli_rejects_piped_input_before_reading_password_or_opening_database(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(command.sys.stdin, "isatty", lambda: False)
    prompt = Mock()
    reset = AsyncMock()
    monkeypatch.setattr(command.getpass, "getpass", prompt)
    monkeypatch.setattr(command, "_reset_password", reset)

    assert command.main(["--username", "admin"]) == 1
    prompt.assert_not_called()
    reset.assert_not_called()
    assert "interactive terminal" in capsys.readouterr().err


@pytest.mark.parametrize("failure", ["mismatch", "echo", "eof", "interrupt"])
def test_cli_input_failure_does_not_touch_database(
    terminal: Mock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    def insecure_prompt(_: str) -> str:
        warnings.warn("Cannot disable terminal echo", getpass.GetPassWarning)
        pytest.fail("The insecure input fallback must never execute")

    effects = {
        "mismatch": [NEW_PASSWORD, "different-password"],
        "echo": insecure_prompt,
        "eof": EOFError(),
        "interrupt": KeyboardInterrupt(),
    }
    terminal.side_effect = effects[failure]
    reset = AsyncMock()
    monkeypatch.setattr(command, "_reset_password", reset)

    assert command.main(["--username", "admin"]) == 1
    reset.assert_not_called()
    output = capsys.readouterr()
    assert "successfully" not in output.out
    assert NEW_PASSWORD not in output.out + output.err


@pytest.mark.parametrize(
    "args", [[], ["--username", " "], ["--username", "admin", "--password", NEW_PASSWORD]]
)
def test_cli_rejects_unsafe_arguments_without_echoing_password(
    terminal: Mock, capsys: pytest.CaptureFixture[str], args: list[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        command.main(args)
    assert exc.value.code == 2
    terminal.assert_not_called()
    assert NEW_PASSWORD not in capsys.readouterr().err


def test_cli_database_failure_hides_secrets(
    terminal: Mock, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    reset = AsyncMock(side_effect=RuntimeError(f"postgresql://user:db-secret@host {NEW_PASSWORD}"))
    monkeypatch.setattr(command, "_reset_password", reset)

    assert command.main(["--username", "admin"]) == 1
    output = capsys.readouterr()
    assert "Password reset failed" in output.err
    assert "db-secret" not in output.out + output.err
    assert NEW_PASSWORD not in output.out + output.err
    assert "Traceback" not in output.err


@pytest.fixture
def admin_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AsyncEngine]:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'admin-reset.sqlite3'}"
    monkeypatch.setattr(settings, "DATABASE_URL", database_url)
    engine = create_async_engine(database_url, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def initialize() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(User.__table__.create)
        async with sessions() as session:
            await auth_service.ensure_default_admin(session, "admin", OLD_PASSWORD)

    try:
        asyncio.run(initialize())
        yield engine
    finally:
        asyncio.run(engine.dispose())


def test_cli_persists_reset_and_login_rejects_old_password(
    admin_database: AsyncEngine, terminal: Mock, capsys: pytest.CaptureFixture[str]
) -> None:
    sessions = async_sessionmaker(admin_database, expire_on_commit=False)

    async def test_db() -> AsyncGenerator[AsyncSession, None]:
        async with sessions() as session:
            yield session

    async def read_users_after_startup() -> list[User]:
        async with sessions() as session:
            await auth_service.ensure_default_admin(session, "admin", OLD_PASSWORD)
            return list((await session.scalars(select(User))).all())

    api = FastAPI()
    api.include_router(auth_router, prefix="/api/v1/auth")
    api.dependency_overrides[get_db] = test_db
    old_hash = asyncio.run(read_users_after_startup())[0].hashed_password
    assert command.main(["--username", "admin"]) == 0
    output = capsys.readouterr()
    assert "reset successfully" in output.out
    assert "Existing access tokens remain valid" in output.out
    assert NEW_PASSWORD not in output.out + output.err
    assert old_hash not in output.out + output.err
    users = asyncio.run(read_users_after_startup())
    assert len(users) == 1
    assert users[0].hashed_password != old_hash
    assert verify_password(NEW_PASSWORD, users[0].hashed_password)

    with TestClient(api) as client:
        old_login = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": OLD_PASSWORD}
        )
        assert old_login.status_code == 401
        new_login = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": NEW_PASSWORD}
        )
        assert new_login.status_code == 200
        token = new_login.json()["access_token"]
        me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        assert me.json()["username"] == "admin"


@pytest.mark.parametrize("failure", ["missing", "short_password", "commit"])
def test_cli_refusal_or_failed_commit_preserves_existing_password(
    admin_database: AsyncEngine,
    terminal: Mock,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    sessions = async_sessionmaker(admin_database, expire_on_commit=False)

    async def stored_hash() -> str:
        async with sessions() as session:
            users = list((await session.scalars(select(User))).all())
            assert len(users) == 1
            assert users[0].username == "admin"
            return users[0].hashed_password

    async def reject_writes() -> None:
        async with admin_database.begin() as connection:
            await connection.exec_driver_sql(
                "CREATE TRIGGER reject_reset BEFORE UPDATE OF hashed_password ON users "
                "BEGIN SELECT RAISE(ABORT, 'private-database-error'); END"
            )

    old_hash = asyncio.run(stored_hash())
    if failure == "commit":
        asyncio.run(reject_writes())
    if failure == "short_password":
        terminal.side_effect = ["short", "short"]
    username = "missing" if failure == "missing" else "admin"
    assert command.main(["--username", username]) == 1
    output = capsys.readouterr()
    assert "reset successfully" not in output.out
    assert "private-database-error" not in output.err
    assert NEW_PASSWORD not in output.out + output.err
    assert old_hash not in output.out + output.err
    assert asyncio.run(stored_hash()) == old_hash


def test_cli_missing_schema_fails_without_creating_accounts_or_tables(
    tmp_path: Path,
    terminal: Mock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import sqlite3

    path = tmp_path / "uninitialized.sqlite3"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    assert command.main(["--username", "admin"]) == 1
    assert "reset failed" in capsys.readouterr().err
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []
        )

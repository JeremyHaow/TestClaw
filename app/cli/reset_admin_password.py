"""Reset an existing admin password using trusted shell and database access."""

import argparse
import asyncio
import getpass
import sys
import warnings
from collections.abc import Sequence


async def _reset_password(username: str, password: str) -> None:
    # Load configuration only after parsing arguments and securely reading input.
    # Do not import app.main: resetting must not bootstrap users or create tables.
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.config import settings
    from app.services.auth_service import auth_service

    engine = create_async_engine(
        settings.DATABASE_URL, echo=False, hide_parameters=True, poolclass=NullPool
    )
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            await auth_service.reset_admin_password(session, username, password)
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reset an existing active administrator using server access and DATABASE_URL.",
        epilog="Requires an interactive terminal. Passwords are never accepted as arguments.",
        allow_abbrev=False,
    )
    parser.add_argument("--username", required=True, help="Exact existing administrator username")
    args, extra = parser.parse_known_args(argv)
    if extra:
        # argparse's default error would echo an accidentally supplied password.
        parser.error("Unsupported arguments. Passwords must be entered at the hidden prompts.")
    if not args.username.strip():
        parser.error("--username cannot be empty")
    if not sys.stdin.isatty():
        sys.stderr.write("An interactive terminal is required; piped passwords are not accepted.\n")
        return 1

    sys.stdout.write(f"Resetting password for administrator {args.username!r}.\n")
    try:
        with warnings.catch_warnings():
            # getpass normally falls back to echoed stdin if terminal control fails.
            warnings.simplefilter("error", getpass.GetPassWarning)
            password = getpass.getpass("New password (12-1024 characters): ")
            confirmation = getpass.getpass("Confirm new password: ")
        if password != confirmation:
            sys.stderr.write("Passwords do not match; no password was changed.\n")
            return 1

        from app.services.auth_service import AdminPasswordResetError

        try:
            asyncio.run(_reset_password(args.username, password))
        except AdminPasswordResetError as exc:
            sys.stderr.write(f"Password reset refused: {exc}\n")
            return 1
    except getpass.GetPassWarning:
        sys.stderr.write("Secure password input is unavailable; no password was changed.\n")
        return 1
    except (EOFError, KeyboardInterrupt):
        sys.stderr.write("Password reset cancelled.\n")
        return 1
    except Exception:
        # Settings/driver/DB errors can contain credentials or SQL parameters.
        sys.stderr.write(
            "Password reset failed. Check DATABASE_URL, database access, and migrations.\n"
        )
        return 1

    sys.stdout.write(
        "Administrator password reset successfully.\n"
        "Existing access tokens remain valid until expiry; see the deployment guide for revocation.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

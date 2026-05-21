import os
import tempfile
from pathlib import Path

_PYTEST_DB_PATH = Path(tempfile.gettempdir()) / "testclaw_pytest.sqlite3"
if _PYTEST_DB_PATH.exists():
    _PYTEST_DB_PATH.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_PYTEST_DB_PATH}"

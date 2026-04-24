# Quality Guidelines

> Code quality standards for backend development.

---

## Required Patterns

- **Type hints** on all function signatures and class attributes
- **`async/await`** for all DB operations and LLM calls
- **Pydantic schemas** for all API request/response bodies
- **`ORMModel`** base (with `from_attributes=True`) for ORM-compatible response schemas
- **`Mapped[]`** annotations for all SQLAlchemy columns
- **`async with`** for session management

## Forbidden Patterns

- **Sync DB calls** — never use `session.query()` or sync `Session`
- **Bare `except:`** — always catch specific exceptions or use `except Exception`
- **`print()` for logging** — use `logging.getLogger(__name__)`
- **Hardcoded secrets** — use `Settings` from `app/config.py`
- **Raising in agent nodes** — always catch and fallback
- **Committing without refresh** — always `await db.refresh(obj)` after commit if returning the object

## Testing Requirements

- Tests in `tests/` directory
- Use `pytest` with `pytest-asyncio` for async tests
- Test files: `test_{module_name}.py`
- Test functions: `test_{behavior_description}`

## Code Review Checklist

- [ ] Type hints present on all public functions
- [ ] No `print()` calls — use `logger`
- [ ] No hardcoded values — use `Settings`
- [ ] Agent nodes have try/except with fallback
- [ ] API routes return proper status codes
- [ ] DB operations use `async/await`
- [ ] No secrets in logs or error messages

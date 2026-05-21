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

## Scenario: Automatic API Auth Preflight

### 1. Scope / Trigger

- Trigger: API runs may target OpenAPI specs whose protected endpoints require a token, but users may only have login credentials such as username, password, captcha code, tenant, and login request body.
- Applies to `app/api/v1/runs.py`, `frontend/src/pages/RunPage.vue`, and API runner state injection through `auth_headers`.
- Purpose: prevent false API runs where positive protected assertions are silently skipped or executed unauthenticated when the user expected the agent to log in first.

### 2. Signatures

- Run payload fields:
  ```python
  auth_config: AuthAcquireConfig | None = None
  ```
- Auth config schema:
  ```python
  class AuthAcquireConfig(BaseModel):
      enabled: bool = False
      username: str | None = None
      password: str | None = None
      captcha: str | None = None
      tenant: str | None = None
      login_url: str | None = None
      method: str = "POST"  # POST, PUT, PATCH
      content_type: str = "json"  # json or form
      headers: dict[str, Any] | None = None
      body: dict[str, Any] | None = None
      token_path: str | None = None  # e.g. access_token or data.token
      header_name: str = "Authorization"
      token_prefix: str = "Bearer"
  ```
- Preflight response fields:
  ```json
  {
    "auth_resolved": true,
    "auth_strategy": "auto_login",
    "auth_header_name": "Authorization",
    "auth_error": null
  }
  ```

### 3. Contracts

- `/api/v1/runs/preflight` must attempt auto-login when `auth_config.enabled=true`.
- `/api/v1/runs` must resolve auto-login again server-side before dispatching the worker; do not trust only the preflight result cached in the browser.
- If auth is required and no usable `Authorization`/API key/Cookie/token-like header can be prepared, run creation must return `400` and must not enqueue the task.
- A successful auto-login injects resolved `auth_headers` into the worker state. A runtime `auth_config` may also be passed to the worker only for refresh/re-acquire, but it must not be included in `Task.execution_log` or tool-call evidence.
- Manual Token/Header is the primary simple path. `auth_config` can supplement it with "refresh on expiry" credentials rather than replacing the manual header path.
- When `auth_config.body` is omitted, build the login body from `username`, `password`, `captcha`, and `tenant`, mapping onto the login endpoint schema fields when available.
- Relative `login_url` values are resolved against the run target/base URL; absolute `http(s)` URLs are used as-is.
- When `login_url` is omitted, infer it from OpenAPI login/auth/token endpoints when possible.
- `token_path` supports simple dot paths such as `access_token`, `data.token`, and `$.data.token`. When omitted, common token fields may be inferred.
- During API execution, if a non-`AUTH` request with an auth-like header returns HTTP `401/403` or JSON envelope `code/status/status_code` `401/403`, the runner may use `auth_config` to refresh once and retry that request once. Record an `api.auth_refresh` tool call with method/url/status metadata only.

### 4. Validation & Error Matrix

- Auth-required API + no token/header + no auto auth -> preflight `auth` check is `missing`, readiness is `blocked`, create run returns `400`.
- Auth-required API + auto auth login returns 4xx/5xx -> preflight is blocked and create run returns `400`.
- Auth-required API + auto auth succeeds but `token_path` is missing -> preflight is blocked and create run returns `400`.
- Auth-required API + auto auth succeeds -> preflight reports `auth_resolved=true`, create run injects `Authorization: Bearer <token>`.
- Non-auth API + auto auth fails -> warn only; do not block the run solely for optional auth failure.
- Manual token + refresh config + request returns envelope `{"code":401}` -> refresh auth, retry that request once, and keep token/password values out of persisted evidence.

### 5. Good/Base/Bad Cases

- Good: user provides `/auth/login`, login JSON body, and `data.token`; preflight proves the token can be acquired and the worker receives an Authorization header.
- Good: user provides a current token plus username/password/captcha/tenant; the runner refreshes after a 401/403 and retries one affected request.
- Base: user provides a direct Bearer token or API key header; preflight treats it as ready without attempting auto-login.
- Bad: user only provides `X-Tenant` or setup notes; protected API run starts anyway and later reports skipped/unauthorized checks as if testing happened.

### 6. Tests Required

- Preflight: protected OpenAPI without credentials returns an `auth` check with `status="missing"`.
- Preflight: auto-login success returns `auth_resolved=true` without exposing the token in JSON.
- Create run: protected OpenAPI without token/header/auto-auth returns `400`.
- Create run: auto-login success dispatches the worker with resolved `auth_headers`.
- API runner: expired manual token + valid refresh config retries one non-`AUTH` request and records `api.auth_refresh` without secrets.
- Frontend build: `RunPage.vue` must compile with manual and auto auth modes.

### 7. Wrong vs Correct

#### Wrong

```python
supplied_auth = bool(payload.token or payload.headers)
run_agent_task.delay(task.id, objective, target_url, auth_headers=payload.headers)
```

#### Correct

```python
prepared_headers, auth_resolution = await _prepare_run_auth(payload, ...)
if auth_required_count and not _has_auth_like_header(prepared_headers):
    raise HTTPException(status_code=400, detail="auth required")
run_agent_task.delay(task.id, objective, target_url, auth_headers=prepared_headers)
```

## Scenario: Pytest Database URL Isolation

### 1. Scope / Trigger

- Trigger: backend tests import `app.main`, `app.database`, or any module that reads `app.config.settings` during module import.
- Why code-spec depth is required: `DATABASE_URL` is an environment contract, and engine creation happens at import time.
- Applies to `tests/conftest.py`, `app/config.py`, `app/database.py`, and any test using `fastapi.testclient.TestClient`.

### 2. Signatures

- Settings source:
  ```python
  class Settings(BaseSettings):
      DATABASE_URL: str = "sqlite+aiosqlite:///./testclaw.db"
  ```
- Engine binding:
  ```python
  engine = create_async_engine(settings.DATABASE_URL, echo=False, pool_pre_ping=True)
  ```
- Test bootstrap:
  ```python
  os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{temp_db_path}"
  ```

### 3. Contracts

- Pytest must override `DATABASE_URL` before importing application modules that bind `settings` or `engine`.
- The override belongs in `tests/conftest.py` so it runs before test module imports.
- Test DBs must be local and deterministic; prefer a temp SQLite file over the developer's `.env` value.
- Production and docker runtime behavior must remain unchanged; only the test process overrides the env var.

### 4. Validation & Error Matrix

- `.env` points to `postgresql+asyncpg://...@db:5432/...` outside docker and tests do not override it -> app startup can fail with name resolution / connection error.
- Tests set `DATABASE_URL` in `tests/conftest.py` before importing `app.main` -> startup uses isolated SQLite and `TestClient` can boot.
- Tests set `DATABASE_URL` after importing `app.main` or `app.database` -> too late; cached settings / engine may still target the old DB.
- No explicit test override and no `.env` override present -> default `sqlite+aiosqlite:///./testclaw.db` is used.

### 5. Good/Base/Bad Cases

- Good: `tests/conftest.py` points pytest to a temp SQLite file and removes stale state before the suite starts.
- Base: tests rely on the default SQLite URL because no docker-specific `DATABASE_URL` is present.
- Bad: a local test run reads `.env` and tries to connect to the docker hostname `db`.

### 6. Tests Required

- Integration: `tests/test_tasks_api.py` must boot `TestClient(app)` successfully without requiring docker services.
- Regression: targeted pytest runs must pass with the repo's checked-in `.env` still present.
- Assertion point: startup reaches auth bootstrap and route handling instead of failing in SQLAlchemy engine connect.

### 7. Wrong vs Correct

#### Wrong

```python
from app.main import app
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./tmp-test.db"
```

#### Correct

```python
# tests/conftest.py
import os
import tempfile
from pathlib import Path

pytest_db_path = Path(tempfile.gettempdir()) / "testclaw_pytest.sqlite3"
if pytest_db_path.exists():
    pytest_db_path.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{pytest_db_path}"
```

## Scenario: Do Not Report Coverage You Did Not Verify

### 1. Scope / Trigger

- Trigger: agent workflow changes affect login, test routing, reporting, or progress UI.
- Applies to workflow nodes, reporter fallbacks, and run detail progress rendering.

### 2. Signatures

- Workflow gate: `login_required and login_verified is False`
- Modality gate: `test_type in {"ui", "api"}`
- Progress inputs: `workflow_steps`, `progress_events`, `current_step`

### 3. Contracts

- Do not treat a generated login attempt as successful unless the post-login state is verified.
- Do not report API execution for a UI-only run or UI execution for an API-only run.
- Do not show near-complete progress for a run that has only reached early workflow nodes.
- Do not let tests assert obsolete legacy behavior after the workflow contract changes.

### 4. Validation & Error Matrix

- Login attempted but still on login page -> fail closed and report the blocker.
- UI-only run with API schema available -> keep API summaries not-applicable, not executed.
- Running run with only early-node progress -> keep progress below late-stage completion bands.

### 5. Good/Base/Bad Cases

- Good: report text matches actual executed steps and verified state.
- Base: missing login instructions yields a skipped login step rather than a false failure.
- Bad: final report claims comprehensive UI coverage while the browser never left the login page.

### 6. Tests Required

- Regression tests for graph routing, modality gating, reporter fallback, and progress behavior assumptions.
- Update or replace legacy tests whenever the workflow contract changes.

### 7. Wrong vs Correct

#### Wrong

```python
assert result["generated_code"]
```

#### Correct

```python
assert _after_ui_login({"login_instructions": "demo", "login_verified": False}) == "reporter"
```

## Scenario: Safe API Agent Execution

### 1. Scope / Trigger

- Trigger: API agent runs execute requests generated from Swagger/OpenAPI sources.
- Why code-spec depth is required: run creation, preflight responses, agent state, runner results, and report summaries share one cross-layer execution contract.
- Applies to `app/api/v1/runs.py`, `app/worker/tasks.py`, `app/agent/state.py`, `app/agent/nodes/source_loader.py`, `app/agent/nodes/api_runner.py`, `app/agent/nodes/reporter.py`, and run-detail UI consumers.

### 2. Signatures

- Request policy field:
  ```python
  api_execution_policy: Literal["safe_read_only", "safe_with_auth", "write_allowed"] = "safe_read_only"
  ```
- Preflight metrics:
  ```python
  {
      "auth_required_count": int,
      "estimated_executable_count": int,
      "estimated_skipped_count": int,
      "api_path_prefix_rewrite": {"from": str, "to": str} | None,
  }
  ```
- Runner result counters:
  ```python
  {"total": int, "executed": int, "skipped": int, "passed": int, "failed": int}
  ```

### 3. Contracts

- `safe_read_only` is the default for unknown or real environments.
- `safe_read_only` and `safe_with_auth` must skip `POST`, `PUT`, `PATCH`, and `DELETE`; skipped requests are not failures.
- `write_allowed` may execute mutation requests only when explicitly selected by the caller.
- Auth-required endpoints must be detected from OpenAPI `security` metadata as well as explicit auth parameters.
- When auth is required but no token/header is provided, safe methods may run unauthorized checks, but positive business assertions must be skipped.
- If a Swagger document is served through a public proxy prefix, the loader may rewrite documented paths such as `/dev-api/*` to the reachable public prefix such as `/api/*`; the rewrite must be surfaced in preflight and run detail state.
- Status matching must consider both HTTP status and common JSON envelope status fields such as `code` or `status`.
- API result entries may include `envelope_status_code`, `failure_type`, and `failure_reason` so reporter output can distinguish backend validation/contract failures from generic runner assertions.

### 4. Validation & Error Matrix

- `safe_read_only` + mutation endpoint -> skip with `skipped=True` and a human-readable `skip_reason`.
- Auth-required endpoint + no credentials + positive assertion -> skip the positive assertion, do not report it as failed.
- Auth-required endpoint + no credentials + expected unauthorized response -> execute and pass when HTTP status or JSON envelope status matches 401/403.
- Swagger path prefix differs from reachable public prefix -> apply and record `api_path_prefix_rewrite`; do not send requests to the internal-only prefix.
- JSON envelope returns `{"code": 401}` with HTTP 200 -> treat as unauthorized for matching and reporting.
- Invalid-input negative case returns HTTP 200 with body `{"code": 500}` -> keep the API case failed and classify it as `backend_validation_contract`, not generic `api_assertion`.

### 5. Good/Base/Bad Cases

- Good: real-environment API run reports `executed=44`, `skipped=93`, `failed=0` when all executable read checks pass and write checks are intentionally skipped.
- Base: no credentials are provided; the report explains skipped auth-positive checks and recommends adding token/header or configuring login.
- Bad: skipped write requests or auth-positive checks are counted as failed, producing a false `BUG_FOUND` run.

### 6. Tests Required

- Regression: source loader rewrites proxied Swagger paths and persists the rewrite contract.
- Regression: auth chain marks endpoints requiring OpenAPI `security` as auth-required.
- Regression: API runner skips mutation methods under safe policies and does not count skips as failures.
- Regression: reporter summaries include executed/skipped counts and keep skipped requests out of failed totals.
- Regression: reporter turns `backend_validation_contract` API result failures into backend validation contract findings.
- Preflight: response exposes executable/skipped/auth-required counts and policy warnings.

### 7. Wrong vs Correct

#### Wrong

```python
failed = [item for item in results if not item["passed"]]
```

#### Correct

```python
failed = [item for item in results if not item.get("skipped") and not item.get("passed")]
skipped = [item for item in results if item.get("skipped")]
```

## Code Review Checklist

- [ ] Type hints present on all public functions
- [ ] No `print()` calls — use `logger`
- [ ] No hardcoded values — use `Settings`
- [ ] Agent nodes have try/except with fallback
- [ ] API routes return proper status codes
- [ ] DB operations use `async/await`
- [ ] No secrets in logs or error messages

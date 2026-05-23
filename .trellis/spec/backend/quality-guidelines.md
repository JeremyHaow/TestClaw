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

## Scenario: Non-Blocking Worker Readiness Preflight

### 1. Scope / Trigger

- Trigger: `/api/v1/runs/preflight` reports whether the async execution path is ready before the user starts a run.
- Applies to `app/api/v1/runs.py`, `app/config.py`, the Celery worker/broker stack, and `frontend/src/pages/RunPage.vue` readiness rendering.
- Purpose: make first-run readiness honest without making local development or tests hang when Redis/Celery is unavailable.

### 2. Signatures

- Settings:
  ```python
  PREFLIGHT_WORKER_TIMEOUT_SECONDS: float = 0.5
  ```
- Backend helpers:
  ```python
  async def _redis_broker_reachable(timeout: float) -> bool
  async def _best_effort_worker_readiness() -> tuple[str, str, str | None]
  ```
- Preflight check payload:
  ```json
  {"key": "worker", "label": "任务 Worker", "status": "ready|warning", "detail": "...", "action": "..."}
  ```

### 3. Contracts

- The worker readiness check is advisory: unavailable Redis or Celery workers must return `status="warning"`, not block run creation.
- Probe Redis with a short socket timeout before calling Celery `inspect().ping()`; do not call Celery inspection when the broker is unreachable.
- If Redis is reachable, Celery worker inspection may run, but it must use `PREFLIGHT_WORKER_TIMEOUT_SECONDS` and return quickly.
- Frontend readiness panels consume the generic `checks[]` list and should display the `worker` check like other preflight checks.

### 4. Validation & Error Matrix

- Redis unavailable -> worker check `warning`, detail explains broker is unavailable, action tells user to start Redis and Worker.
- Redis available but no worker replies -> worker check `warning`, detail explains no active Worker was detected.
- Worker replies to Celery ping -> worker check `ready`, detail includes active worker count.
- Worker probe raises any exception -> worker check `warning`; do not raise a 500 from preflight.

### 5. Good/Base/Bad Cases

- Good: Docker stack is running, preflight shows `任务 Worker` ready before the user starts a long run.
- Base: local developer has no Redis running, preflight still responds quickly and warns that synchronous fallback may be used.
- Bad: every preflight request waits on Celery broker connection timeouts when Redis is down.

### 6. Tests Required

- Unit: `_best_effort_worker_readiness()` returns a warning when `_redis_broker_reachable()` is false.
- Integration: `/api/v1/runs/preflight` includes a `worker` check and preserves the rest of the readiness payload.
- Frontend build: `RunPage.vue` compiles with the readiness panel wording and generic check rendering.

### 7. Wrong vs Correct

#### Wrong

```python
replies = celery_app.control.inspect(timeout=5).ping()
```

#### Correct

```python
if not await _redis_broker_reachable(timeout):
    return "warning", "Redis broker unavailable", "Start Redis and Worker"
replies = await asyncio.to_thread(_ping_workers)
```

## Scenario: Structured Mission Preview Preflight

### 1. Scope / Trigger

- Trigger: `/api/v1/runs/preflight` must show the user what the testing agent inferred before a run is created.
- Applies to `app/api/v1/runs.py`, `frontend/src/pages/RunPage.vue`, and preflight tests.
- Purpose: make starting a run feel like handing a mission to an agent, while keeping existing clients compatible through additive response fields.

### 2. Signatures

- Additive response field:
  ```json
  {
    "mission_preview": {
      "handoff": "预检完成但有待确认项：确认后可启动测试智能体。",
      "readiness": "ready|needs_review|blocked",
      "target": "https://api.example.test",
      "input_mode": "Swagger/OpenAPI JSON",
      "test_mode": "API 检查",
      "objective": "验证 API 契约、参数边界、鉴权路径和错误分支。",
      "scope": "文档包含 2 个端点，预计执行 1 个接口，策略跳过 1 个变更接口。",
      "execution_policy": "安全只读；默认跳过 POST/PUT/PATCH/DELETE，避免误改真实数据。",
      "safety_boundary": "已提供前置说明/安全边界；预览不展开可能包含凭据的原文。",
      "auth_readiness": "已提供 Token/Header；预览不展示任何鉴权值。",
      "counts": {
        "endpoint_count": 2,
        "estimated_executable_count": 1,
        "estimated_skipped_count": 1,
        "auth_required_count": 1,
        "flow_step_count": 5,
        "check_count": 8,
        "ready_count": 6,
        "review_count": 1,
        "blocked_count": 0
      },
      "correction_prompts": []
    }
  }
  ```

### 3. Contracts

- `mission_preview` is additive; keep existing top-level fields such as `checks`, `readiness`, `endpoint_count`, and `auth_resolved`.
- Build the preview from server-side inference, not from frontend-only heuristics.
- The preview may include header names such as `Authorization`, but must not include raw token, cookie, API key, password, or auth header values.
- Do not echo `setup_instructions` in preview because it may contain credentials; summarize whether it was supplied.
- Convert warning/missing checks and warnings into `correction_prompts[]` with a concrete action.
- For pasted OpenAPI JSON/YAML without an inferred server/base URL, use a label such as pasted OpenAPI document instead of echoing the raw source.

### 4. Validation & Error Matrix

- Protected API + no auth -> preview readiness `blocked`, auth correction prompt is present.
- Protected API + manual token/header -> preview auth readiness says values are hidden, no raw secret appears in JSON.
- Auto-auth success -> preview can name the resolved header, not the resolved token.
- Safe read-only policy + write endpoints -> counts include `estimated_skipped_count`.
- Missing Worker/provider/runner readiness -> preview includes review/blocking counts and correction prompts without blocking unrelated checks.

### 5. Good/Base/Bad Cases

- Good: user sees target, inferred mode, objective, scope counts, policy, auth state, and exact fix prompts before launch.
- Base: old clients ignore `mission_preview` and continue consuming legacy fields.
- Bad: frontend reconstructs the mission from form fields and accidentally displays a pasted token or login setup text.

### 6. Tests Required

- Integration: `/api/v1/runs/preflight` returns `mission_preview` with target/mode/objective/scope/counts.
- Regression: manual token/header/auth config secrets do not appear in the serialized preflight response.
- Regression: auth-required preflight without credentials includes an auth correction prompt.
- Frontend build: `RunPage.vue` compiles with the typed `mission_preview` contract.

### 7. Wrong vs Correct

#### Wrong

```python
mission_preview = {"auth": payload.headers, "setup": payload.setup_instructions}
```

#### Correct

```python
mission_preview = {
    "auth_readiness": "已提供 Token/Header；预览不展示任何鉴权值。",
    "safety_boundary": "已提供前置说明/安全边界；预览不展开可能包含凭据的原文。",
}
```

## Scenario: Target Memory Preflight

### 1. Scope / Trigger

- Trigger: `/api/v1/runs/preflight` should tell testers what the agent has learned about the inferred target before launch.
- Applies to `app/api/v1/runs.py`, `frontend/src/pages/RunPage.vue`, and preflight tests.
- Purpose: make previous runs, blockers, repeat failures, reusable suites, and next-run strategy influence the start flow without adding schema.

### 2. Signatures

- Additive response field:
  ```json
  {
    "target_memory": {
      "target": "https://api.example.test",
      "previous_run_count": 3,
      "target_run_count": 1,
      "host_run_count": 3,
      "last_run": {"run_id": "...", "status": "bug_found", "test_type": "api", "created_at": "..."},
      "recurring_failure_themes": [],
      "known_blockers": [],
      "reusable_suite_count": 1,
      "reusable_case_count": 4,
      "reusable_suites": [],
      "suggested_strategy": "...",
      "confidence": "low|medium|high",
      "confidence_reason": "..."
    }
  }
  ```

### 3. Contracts

- `target_memory` is additive; keep existing preflight fields and `mission_preview` unchanged for existing clients.
- Derive memory from existing `Task.execution_log`, run status/test type/timestamps, triage/intervention helpers, and linked `TestSuite` rows. Do not add database columns for this surface.
- Match URL targets primarily by host, while also reporting exact target counts when paths match.
- Build execution-log-derived memory only from redacted data and short synthesized fields.
- Do not expose raw setup instructions, auth configs, headers, tokens, cookies, passwords, API keys, request bodies, stdout/stderr, Playwright fill/type values, or URL query values.
- For new targets, return low-confidence memory with zero counts, empty recurring/blocker/suite lists, and a startup strategy.
- Frontend should render the server-provided object directly; do not reconstruct memory from form fields.

### 4. Validation & Error Matrix

- No previous runs for host/target -> return `previous_run_count=0`, empty memory arrays, and `confidence="low"`.
- Previous runs with raw or legacy unredacted logs -> redact before building themes, blockers, suite labels, strategy, or target labels.
- URL target contains query values -> do not include query values in `target_memory.target`, surfaces, themes, or blocker details.
- Matching target has linked suites -> include suite labels and case counts from `TestSuite.task_id`, with labels redacted.
- Matching target has setup/auth failures -> summarize blocker category and safe detail; do not include credentials or setup text verbatim.
- More rows than the sample limit -> derive memory from the bounded recent sample only.

### 5. Good/Base/Bad Cases

- Good: repeated host history returns last status, recurring sanitized failure theme, known auth/setup blockers, reusable suite counts, and a concrete strategy.
- Base: a single previous failed run returns medium confidence and known blockers but no recurring theme unless the theme repeats.
- Base: a new target returns low confidence with a startup strategy and empty memory lists.
- Bad: target memory echoes `setup_instructions`, `auth_config`, request bodies, headers, query parameter values, stdout/stderr, or Playwright fill/type values.

### 6. Tests Required

- Preflight: repeated target history returns non-zero counts, last run status, recurring themes, reusable suite/case counts, and high/medium confidence.
- Preflight: setup/auth blockers are summarized without leaking raw credentials.
- Regression: target memory JSON does not include secret-bearing log fields or URL query values.
- Preflight: a new target returns low-confidence empty memory.
- Frontend build: `RunPage.vue` compiles with the typed `target_memory` contract.

### 7. Wrong vs Correct

#### Wrong

```python
target_memory = {
    "target": task.target_url,
    "known_blockers": [json.loads(task.execution_log)["setup_instructions"]],
    "last_headers": json.loads(task.execution_log)["auth_config"]["headers"],
}
```

#### Correct

```python
parsed = redact_sensitive_data(_parse_execution_log_dict(task.execution_log))
triage = _build_run_triage_summary(_history_status(task), parsed)
setup_blocked, setup_reason = _setup_intervention_signal(parsed)
target_memory = {
    "target": _target_memory_text(_preflight_target_label(source, input_type, target_url)),
    "known_blockers": [{"category": "setup_auth", "detail": _target_memory_text(setup_reason)}] if setup_blocked else [],
    "recurring_failure_themes": [
        _target_memory_text(finding.get("title"))
        for finding in _triage_list(triage.get("blocking_findings"))
    ],
}
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

## Scenario: Run Detail Triage Summary

### 1. Scope / Trigger

- Trigger: `/api/v1/runs/{run_id}` exposes final run detail after reporter/API/UI execution.
- Applies to `app/api/v1/runs.py`, `app/core/redaction.py`, and `frontend/src/pages/RunDetailPage.vue`.
- Purpose: make run detail a release triage artifact without requiring testers to inspect raw execution JSON.

### 2. Signatures

- Additive response field:
  ```json
  {
    "triage_summary": {
      "summary": "All executed checks passed.",
      "release_risk": {"level": "low|medium|high|unknown", "label": "...", "rationale": "..."},
      "blocking_count": 0,
      "blocking_findings": [],
      "affected_surfaces": [],
      "evidence": {"count": 1, "api_result_count": 1, "screenshot_count": 0, "tool_call_count": 0},
      "confidence": {"level": "high|medium|low", "rationale": "..."},
      "recommended_next_actions": [],
      "reproduction": {"available": false, "script_available": false, "script_field": null, "steps": []},
      "triage_flow": []
    }
  }
  ```

### 3. Contracts

- `triage_summary` is additive; keep existing `final_report`, `api_execution_result`, `ui_execution_result`, cases, artifacts, and logs unchanged for existing consumers.
- Build triage from redacted run detail data, not raw execution secrets.
- Do not include raw setup instructions, auth configs, auth header values, tokens, cookies, passwords, request bodies, stdout, or stderr dumps in triage text.
- A failed or bug-found run with blocking findings must surface release risk, affected endpoints/pages, evidence count, next action, confidence, and reproduction steps when available.
- A passing run must return `release_risk.level="low"`, `blocking_count=0`, an evidence count derived from execution results/artifacts, and a non-empty next action.
- Reproduction affordances should point to existing UI/API result tabs, screenshots, and generated script fields; do not synthesize commands that require secret headers.

### 4. Tests Required

- Run detail: bug/failure execution log returns `triage_summary` with high risk, blocking findings, affected surface, evidence count, and no leaked secrets.
- Run detail: pass/no-bug execution log returns low risk, zero blocking findings, evidence count, and no reproduction steps.
- Frontend build: `RunDetailPage.vue` compiles when rendering `triage_summary`.

## Scenario: Run Triage Export Handoff

### 1. Scope / Trigger

- Trigger: testers need a stable artifact for release review, bug filing, or team handoff after a run completes.
- Applies to `GET /api/v1/runs/{run_id}/triage-export`, redacted run detail state, saved suite metadata, and `frontend/src/pages/RunDetailPage.vue`.
- Purpose: export the same triage decision data shown in run detail without requiring users to copy raw logs or reconstruct report context manually.

### 2. Signatures

- Endpoint:
  ```text
  GET /api/v1/runs/{run_id}/triage-export?format=markdown|json
  ```
- JSON response top-level fields:
  ```json
  {
    "export_version": "triage_export.v1",
    "run": {"id": "...", "status": "bug_found", "test_type": "api", "target": "https://api.example.test/path"},
    "summary": "...",
    "release_risk": {"level": "high", "label": "...", "rationale": "..."},
    "blocking_findings": [],
    "affected_surfaces": [],
    "evidence_summary": {"count": 2, "api_result_count": 1, "screenshot_count": 0, "tool_call_count": 1},
    "reproduction": {"available": true, "script_available": false, "steps": []},
    "recommended_next_actions": [],
    "reusable_assets": {"saved_suite_count": 1, "saved_case_count": 2},
    "safe_links": {"run_detail_path": "/runs/..."}
  }
  ```

### 3. Contracts

- `format=markdown` returns `text/markdown`; `format=json` returns the same safe structured payload as JSON. Unsupported formats return `400`; unknown runs return `404`.
- Build the export from redacted execution-log data and `_build_run_triage_summary(...)`; do not add database columns or expose raw logs.
- Markdown should be concise and tester-facing: run metadata, release risk, blocking findings with severity/confidence, affected surfaces, evidence summary, reproduction steps, recommended next actions, reusable assets/suite info, and safe run identifiers/paths.
- JSON and Markdown must not expose raw setup instructions, auth config, auth/header values, request bodies, URL query params, cookies, sessions, JWT, CSRF/XSRF, Playwright fill/type values, stdout/stderr dumps, reproducible script values, or supplemental intervention inputs.
- Include saved suite metadata only by safe identifiers, redacted suite names, and case counts. Do not include saved case bodies in the export.
- Frontend download buttons should fetch the backend export artifact; the browser must not reconstruct the export payload from local run state.

### 4. Validation & Error Matrix

- `GET /runs/{id}/triage-export?format=json` -> safe JSON payload with export version, run metadata, risk, findings, evidence, actions, reusable assets, and safe links.
- `GET /runs/{id}/triage-export?format=markdown` -> Markdown attachment with the same sections and no secret-bearing strings.
- `GET /runs/{missing}/triage-export?format=json` -> `404 Run not found`.
- `GET /runs/{id}/triage-export?format=csv` -> `400 format must be markdown or json`.
- Execution logs containing old unredacted setup/auth/request/query/body/Playwright/intervention data -> export contains only redacted or synthesized safe text.

### 5. Good/Base/Bad Cases

- Good: a bug-found API run exports a Markdown handoff with `GET /private`, high release risk, evidence counts, reproduction steps, next actions, saved suite counts, and `/runs/{id}`.
- Base: a passing run exports low risk, no blocking findings, evidence counts, and release-review archiving guidance.
- Bad: export includes `Authorization`, cookies, `?token=...`, request JSON bodies, login setup text, Playwright typed values, or raw reproducible script content.

### 6. Tests Required

- Export JSON shape includes run metadata, release risk, blocking findings, evidence summary, reusable asset counts, and safe links.
- Export Markdown shape includes release risk, blocking findings, affected surfaces, evidence summary, reproduction, next actions, reusable assets, and safe links.
- Error tests cover missing run and invalid format.
- Regression: serialized JSON and Markdown exports do not contain setup/auth/header/body/query/cookie/session/JWT/CSRF/XSRF/Playwright/intervention/script secret values.
- Frontend build: `RunDetailPage.vue` compiles with Markdown/JSON export download buttons.

### 7. Wrong vs Correct

#### Wrong

```python
return {
    "execution_log": json.loads(task.execution_log),
    "markdown": frontend_supplied_markdown,
}
```

#### Correct

```python
parsed = redact_sensitive_data(_parse_execution_log_dict(task.execution_log))
triage = _build_run_triage_summary(_status_value(task.status), parsed)
export = await _build_run_triage_export(db, task, parsed, triage)
```

## Scenario: Assisted Run Intervention Rerun

### 1. Scope / Trigger

- Trigger: a run is failed, cancelled, bug-found with blocking context, or still active but blocked by missing login/setup/auth/environment information.
- Applies to `app/api/v1/runs.py`, redacted `Task.execution_log` rendering, and `frontend/src/pages/RunDetailPage.vue`.
- Purpose: let a tester add missing human context and create a new rerun without exposing credentials in API responses or logs.

### 2. Signatures

- Detail additive field:
  ```json
  {
    "intervention_summary": {
      "useful": true,
      "category": "setup_auth|api_auth|environment|run_blocker|triage_followup|none",
      "reason": "Pre-test setup verification failed: password=[REDACTED]",
      "suggested_inputs": [],
      "recommended_action": "补充测试账号、登录步骤、验证码/租户/角色和成功判断后，发起辅助重跑。",
      "assisted_rerun_enabled": true,
      "requires_cancel_current": false,
      "can_cancel_current": false,
      "status": "failed"
    }
  }
  ```
- Endpoint:
  ```text
  POST /api/v1/runs/{run_id}/interventions
  {"supplemental_instructions": "...", "cancel_current": false}
  ```

### 3. Contracts

- `intervention_summary` is derived from redacted run detail state only: `last_error`, `setup_result`, `login_result`, `login_verified`, API/UI skipped indicators, status, and triage summary.
- Assisted rerun must reuse `_rerun_context_from_task(...)` so source input, URL roles, cases, setup/login instructions, API policy, and safe custom headers survive.
- Supplemental instructions are appended to both `setup_instructions` and `login_instructions` for the new worker dispatch.
- Active queued/running source runs require `cancel_current=true`; otherwise return `400` with a clear message.
- When `cancel_current=true`, cancel the source run through the normal cancellation helper before creating the new queued run.
- Sensitive/redacted headers from stored execution logs must not be replayed. Supplemental instructions may contain credentials for the worker, but returned `TaskRead`, run detail, SSE, and persisted execution logs must pass through existing redaction helpers.
- Header rehydration must skip any stored header whose name is sensitive, whose value contains `[REDACTED]`, or whose value would change under `redact_sensitive_text(...)`; only safe custom headers survive.
- Redaction must cover Playwright-style `fill`/`type` commands targeting password/auth/session/captcha/MFA/OTP fields, because those commands may appear in login evidence, case assets, SSE snapshots, or legacy logs.
- SSE snapshot payloads should attach additive `triage_summary` and `intervention_summary` computed from redacted log data so the run detail page can update the intervention panel during active runs.
- Existing `/rerun` and `/cancel` behavior must remain available.

### 4. Validation & Error Matrix

- Unknown run id -> `404 Run not found`.
- Blank `supplemental_instructions` after trimming -> `400 supplemental_instructions is required`.
- Source status `queued`/`running` and `cancel_current=false` -> `400` explaining that the run is still active and must be cancelled before assisted rerun.
- Source status `queued`/`running` and `cancel_current=true` -> cancel the source run, preserve cancellation log keys, then create a queued assisted rerun.
- Source status not in `failed|bug_found|cancelled|queued|running` -> `400` explaining supported source states.
- Stored `Authorization`, `Cookie`, token-like, API-key, or `[REDACTED]` headers -> omitted from worker header rehydration.
- Supplemental text containing `password=...`, `captcha=...`, `otp=...`, bearer/basic tokens, cookies, sessions, or API keys -> raw value may be passed to the worker but must not appear in returned JSON or persisted/rendered execution logs.
- Stored non-sensitive header name with value `Bearer [REDACTED]` or `password=...` -> omitted from worker header rehydration.
- Stored/logged command `fill "Captcha" "1234"`, `type "#mfa" "123456"`, or `fill "#otp" "999999"` -> rendered and saved surfaces contain `[REDACTED]`, not the raw value.

### 5. Good/Base/Bad Cases

- Good: a failed login/setup run returns `intervention_summary.category="setup_auth"`, the tester adds credentials/context, and the assisted rerun receives appended setup/login instructions.
- Good: an active blocked run with `cancel_current=true` is cancelled through the same helper as `/cancel`, then a new queued rerun is dispatched from the rehydrated context.
- Base: an API auth run with skipped auth-positive checks returns `category="api_auth"` and asks for Token/Header or auto-login fields.
- Base: a cancelled run can be retried with supplemental environment/setup context.
- Bad: endpoint creates a rerun from only `Task.target_url`, losing selected suite cases, UI seed URL, API base URL, or safe custom headers.
- Bad: endpoint returns raw supplemental instructions or persists raw credentials into `Task.execution_log`.
- Bad: active runs are silently rerun while the old worker keeps running.

### 6. Tests Required

- Detail: setup/login/auth blocker returns `intervention_summary.useful=true` without leaking secrets.
- Detail: API auth skipped/401/403 blockers produce an API auth intervention reason.
- Endpoint: assisted rerun creates a queued task and dispatches worker kwargs with appended setup/login instructions.
- Endpoint: active run without `cancel_current` returns `400`; active run with `cancel_current=true` cancels then dispatches the assisted rerun.
- Regression: submitted supplemental secrets do not appear in endpoint responses or run-detail execution-log rendering.
- Regression: redacted or secret-looking stored custom header values are not replayed to the worker; safe custom headers still survive.
- Regression: captcha/MFA/OTP keys and Playwright `fill`/`type` command values are redacted in `redact_json_text(...)` and durable case assets.

### 7. Wrong vs Correct

#### Wrong

```python
rerun_context = {"source_input": task.target_url}
rerun_context["setup_instructions"] = payload.supplemental_instructions
return rerun_context
```

#### Correct

```python
rerun_context = _rerun_context_from_task(task)
combined_setup = _append_intervention_instructions(
    rerun_context.get("setup_instructions"),
    payload.supplemental_instructions,
)
rerun_context["setup_instructions"] = combined_setup
rerun_context["login_instructions"] = combined_setup
```

#### Wrong

```python
if task.status == TaskStatus.RUNNING:
    create_rerun_without_cancelling(task)
```

#### Correct

```python
if current_status in {"queued", "running"}:
    if not payload.cancel_current:
        raise HTTPException(status_code=400, detail="Run is still active")
    await _cancel_active_task(db, task, "Run cancelled before assisted intervention rerun")
```

## Scenario: Run History Quality Memory

### 1. Scope / Trigger

- Trigger: `/api/v1/runs/insights` summarizes recent run history for the History page.
- Applies to `app/api/v1/runs.py`, `app/core/redaction.py`, and `frontend/src/pages/HistoryPage.vue`.
- Purpose: make History a testing-agent memory view for quality trends, recurring issues, affected targets/surfaces, evidence availability, and recommended next actions.

### 2. Signatures

- Endpoint:
  ```text
  GET /api/v1/runs/insights?days=30&limit=100
  ```
- Additive response shape:
  ```json
  {
    "window_days": 30,
    "sample_limit": 100,
    "window_run_count": 4,
    "analyzed_runs": 4,
    "status_counts": {"total": 4, "succeeded": 1, "failed": 2, "bug_found": 1, "pass_rate": 25.0, "issue_rate": 75.0},
    "quality_trend": {"direction": "improving|regressing|stable|insufficient", "buckets": []},
    "affected_targets": [],
    "affected_surfaces": [],
    "recurring_themes": [],
    "evidence_reproduction": {"runs_with_evidence": 3, "runs_with_reproduction": 2},
    "recommended_next_actions": []
  }
  ```

### 3. Contracts

- The endpoint is additive and must not change the existing `GET /api/v1/runs` list behavior or response headers.
- Derive insights from existing `Task` rows and `Task.execution_log`; do not add database schema for this surface.
- Bound the analysis with `days` and `limit` query params. Defaults are `days=30` and `limit=100`; keep maximums finite.
- Build execution-log-derived memory from redacted data only. Reuse `redact_sensitive_data`, `redact_json_text`, and triage helpers so older unredacted rows cannot leak through.
- Do not include raw setup instructions, auth configs, tokens, cookies, passwords, API keys, auth headers, request/response bodies, stdout, stderr, or secret-bearing query values.
- It is acceptable to expose redacted target URLs, API methods plus paths, counts, statuses, evidence counts, recurrence labels, severity, and synthesized next actions.
- Recurring themes should only be returned when the same sanitized issue theme appears more than once.
- History UI must consume `/runs/insights` independently from `/runs`, preserving filters, pagination, delete, and run-detail navigation.

### 4. Validation & Error Matrix

- No Task rows in the window -> return `analyzed_runs=0`, empty memory lists, and a startup next action.
- More rows than `limit` -> return `window_run_count` for the full window and derive detailed memory from the bounded sample only.
- `days < 1`, `days > 90`, `limit < 1`, or `limit > 200` -> FastAPI query validation rejects the request.
- Repeated sanitized theme appears once -> do not include it in `recurring_themes`.
- Repeated sanitized theme appears more than once -> include it with count, severity, surfaces, examples, and a recommended action.
- Secret-looking values appear in old `execution_log` rows -> response contains only redacted values or synthesized summaries.

### 5. Good/Base/Bad Cases

- Good: recent failed and bug-found API runs on the same endpoint produce affected target memory, affected surface memory, a recurring API theme, evidence counts, and a next action.
- Base: only passing runs exist; response reports low issue rate, no recurring themes, and recommends preserving evidence for release review.
- Base: active queued/running runs exist; response includes active counts and a next action to wait for completion before confirming trend.
- Bad: history endpoint echoes `setup_instructions`, `auth_config`, request headers, token query values, stdout, or stderr from `execution_log`.
- Bad: route is registered after `/{run_id}` and `/runs/insights` is treated as a run id.

### 6. Tests Required

- Insights: controlled Task history returns status counts, trend, affected targets/surfaces, recurring themes, evidence/reproduction counts, and next actions.
- Regression: secrets embedded in execution logs, auth config, setup instructions, headers, and query values do not appear in the serialized insights response.
- Regression: `GET /api/v1/runs/insights` is registered before `/{run_id}` and returns the insights response, not a run-detail 404.
- Frontend build: `HistoryPage.vue` compiles after rendering quality-memory panels.

### 7. Wrong vs Correct

#### Wrong

```python
@router.get("/{run_id}")
async def get_run_detail(...):
    ...

@router.get("/insights")
async def get_run_history_insights(...):
    return {"logs": [json.loads(task.execution_log) for task in tasks]}
```

#### Correct

```python
@router.get("/insights", response_model=RunHistoryInsightsResponse)
async def get_run_history_insights(...):
    parsed = redact_sensitive_data(_parse_execution_log_dict(task.execution_log))
    triage = _build_run_triage_summary(status, parsed)
    return _build_run_history_insights(...)
```

## Scenario: Run Generated Case Asset Save Contract

### 1. Scope / Trigger

- Trigger: a completed or in-progress run has generated `api_cases`, `ui_cases`, or legacy `test_cases`, and the user accepts selected cases into reusable test assets.
- Applies to `POST /api/v1/runs/{run_id}/case-assets`, `TestCase`, `TestSuite`, suite run payload normalization, and `frontend/src/pages/RunDetailPage.vue`.
- Purpose: make generated cases reviewable assets without adding database schema or persisting credentials from run logs/setup/auth.

### 2. Signatures

- Request:
  ```json
  {
    "suite_name": "Accepted smoke suite",
    "cases": [
      {"source": "api_cases", "index": 0, "case": {"title": "Edited API", "priority": "P1"}},
      {"source": "ui_cases", "index": 1, "case": {"steps": ["Open dashboard"]}}
    ]
  }
  ```
- Response:
  ```json
  {
    "suite_id": "uuid",
    "suite_name": "Accepted smoke suite",
    "case_ids": ["uuid"],
    "cases": [{"id": "uuid", "source": "api_cases", "source_index": 0, "case_type": "api"}],
    "total": 1
  }
  ```

### 3. Contracts

- Parse case sources from `Task.execution_log`; supported sources are `api_cases`, `ui_cases`, and legacy `test_cases`.
- Validate every selected `{source, index}` against the original run-log list index. Invalid, non-object, or duplicate selections return `400`; unknown run ids return `404`; empty selections return `400`.
- Persist accepted cases as `TestCase` rows and create a `TestSuite` with `test_case_ids`; no schema migration is required.
- Saved `TestCase.test_data` may contain `request_template`, `playwright_commands`, target/base URL hints, and `case_asset` metadata with `version=1`, `source_run_id`, source name, source index, and case type.
- Do not persist raw setup instructions, auth config, auth headers, cookies, API keys, bearer/basic tokens, passwords, sessions, auth query/body values, or other secret-bearing query values. Sensitive headers should be omitted from saved request templates; sensitive text/body/query values must be redacted before insert.
- Keep saved case fields focused on title, steps, expected result, priority, category, and safe runner metadata needed by suite reuse.
- The run-detail cases tab may send light edits for title, priority, category, steps, and expected result; backend must merge edits with the original generated case so request/playwright metadata is preserved safely.

### 4. Validation & Error Matrix

- `POST /runs/{missing}/case-assets` -> `404 Run not found`.
- `cases=[]` -> `400 No accepted cases selected`.
- `{"source": "api_cases", "index": 999}` -> `400 Invalid case selection`.
- `{"source": "api_cases", "index": 1}` where source item `1` is a planner note/string -> `400 Invalid case selection`.
- Repeating the same `{source, index}` -> `400 Duplicate case selection`.
- Generated request headers contain `Authorization`, `Cookie`, or `X-API-Key` -> saved `request_template.headers` excludes those keys.
- Generated URLs, text fields, request bodies, auth/session query values, or Playwright fill/type commands contain sensitive values -> saved rows contain redacted values only.

### 5. Good/Base/Bad Cases

- Good: user accepts one edited API case and one edited UI case; backend creates two `TestCase` rows, keeps request/playwright metadata safe, and returns a new `TestSuite` containing both ids.
- Base: user accepts a legacy `test_cases` item; backend infers API/UI type from request templates, Playwright commands, or category labels.
- Base: user omits `suite_name`; backend creates a bounded default name from the run objective.
- Bad: endpoint stores the whole generated case or execution log into `test_data`, carrying `setup_instructions`, auth config, raw headers, stdout, or secret-bearing URLs into durable assets.
- Bad: frontend sends accepted cases by copied payload only and backend cannot prove they came from the run log.

### 6. Tests Required

- Integration: saving accepted run-generated API/UI cases creates `TestCase` rows and a `TestSuite` linked to those ids.
- Regression: invalid selections return `400` and do not create durable assets.
- Regression: sparse or mixed-type source lists preserve original run-log indexes and reject non-object source entries.
- Regression: setup/auth/header/body/query/Playwright secrets do not appear in saved case rows or response JSON.
- Frontend build: `RunDetailPage.vue` compiles after accept/edit/reject and suite save controls.

### 7. Wrong vs Correct

#### Wrong

```python
test_case = TestCase(
    title=edited_case["title"],
    steps=edited_case.get("steps", []),
    test_data=original_case,  # may include auth headers/setup/log-derived data
)
```

#### Correct

```python
normalized = _normalize_case_asset_for_save(
    _case_asset_merge_case(original_case, edited_case),
    run_id=run_id,
    source="api_cases",
    source_index=0,
)
test_case = TestCase(**normalized)
```

## Code Review Checklist

- [ ] Type hints present on all public functions
- [ ] No `print()` calls — use `logger`
- [ ] No hardcoded values — use `Settings`
- [ ] Agent nodes have try/except with fallback
- [ ] API routes return proper status codes
- [ ] DB operations use `async/await`
- [ ] No secrets in logs or error messages

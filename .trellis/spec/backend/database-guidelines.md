# Database Guidelines

> Database patterns and conventions for this project.

---

## ORM

- **SQLAlchemy 2.0** with async support (`AsyncSession`, `create_async_engine`)
- **DeclarativeBase** for model definitions
- `Mapped[]` type annotations with `mapped_column()`
- Database: SQLite (dev) via `aiosqlite`, configurable via `DATABASE_URL` env var

## Session Management

- Session factory: `AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)`
- Dependency injection: `DbSession = Annotated[AsyncSession, Depends(get_db)]`
- Always use `async with AsyncSessionLocal() as session:` or the FastAPI dependency

## Scenario: Celery Worker Async DB Session Loop Ownership

### 1. Scope / Trigger

- Trigger: Celery worker tasks call `asyncio.run(...)` per task, creating a fresh event loop for every agent run.
- Applies to `app/worker/tasks.py` and any future Celery task that opens async SQLAlchemy sessions.
- Purpose: prevent asyncpg pooled connections created in one event loop from being reused or disposed in another event loop.

### 2. Signatures

- Worker engine factory:
  ```python
  def _create_worker_engine() -> AsyncEngine
  ```
- Worker session factory:
  ```python
  def _create_worker_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]
  ```
- Worker session scope:
  ```python
  async with _worker_session_scope() as db:
      ...
  ```

### 3. Contracts

- FastAPI routes continue to use `app.database.engine` and `app.database.AsyncSessionLocal`.
- Celery worker tasks must not import or use the global API `AsyncSessionLocal` for task execution.
- Celery worker tasks must create a worker-task-local async engine with `poolclass=NullPool`.
- The worker engine must be disposed inside the same coroutine/event loop that used it.
- Agent state must still receive `state["db_session"] = db` so progress persistence, model/provider lookup, memory, and report persistence keep using the active task session.
- Do not call `app.database.engine.dispose()` from worker tasks; that engine belongs to the API process/session factory.

### 4. Validation & Error Matrix

- Consecutive Celery tasks in one worker process -> no asyncpg "Future attached to a different loop" connection close errors.
- Worker task starts after a previous task completed -> it creates a fresh worker engine/session and does not reuse prior asyncpg connections.
- Progress persistence during a task -> uses the task-local `db_session` and preserves the existing `Task.execution_log` merge contract.
- FastAPI API request handling -> still uses the global API session factory without worker `NullPool` changes.

### 5. Good/Base/Bad Cases

- Good: `_run()` opens `async with _worker_session_scope() as db`, injects `db` into agent state, and disposes the worker engine in `finally`.
- Base: local SQLite tests use the same factory contract and can run sequential `asyncio.run()` loops without connection reuse.
- Bad: worker imports `AsyncSessionLocal` from `app.database` and then calls `await engine.dispose()` before or after the task.

### 6. Tests Required

- Unit: worker engine uses `NullPool` and is distinct from `app.database.engine`.
- Unit: worker sessionmaker binds sessions to the worker engine and keeps `expire_on_commit=False`.
- Regression: worker session scope can open real sessions across consecutive `asyncio.run()` loops.
- Integration/smoke: PostgreSQL/asyncpg Celery worker can run consecutive tasks without cross-loop connection close errors.

### 7. Wrong vs Correct

#### Wrong

```python
from app.database import AsyncSessionLocal, engine

async def _run(...):
    await engine.dispose()
    async with AsyncSessionLocal() as db:
        ...
```

#### Correct

```python
engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
session_factory = async_sessionmaker(engine, expire_on_commit=False)
try:
    async with session_factory() as db:
        state["db_session"] = db
        ...
finally:
    await engine.dispose()
```

## Query Patterns

```python
# Fetch by primary key
task = await db.get(Task, task_id)

# Select with filter
result = await db.execute(select(Task).where(Task.status == "running"))
items = list(result.scalars())

# Count
count_stmt = select(func.count()).select_from(base.subquery())
total = (await db.execute(count_stmt)).scalar_one()

# Pagination
base = select(Task).order_by(Task.created_at.desc()).offset(offset).limit(page_size)
```

## Write Patterns

```python
# Create
task = Task(objective="test", target_url="https://...")
db.add(task)
await db.commit()
await db.refresh(task)

# Update (modify then commit)
task.status = TaskStatus.SUCCEEDED
await db.commit()
await db.refresh(task)

# Delete
await db.delete(task)
await db.commit()
```

## Scenario: Management Settings Asset API Contract

### 1. Scope / Trigger

- Trigger: settings pages for providers, environments, test cases, and knowledge are product workflows, not static admin dashboards.
- Applies to `app/api/v1/environments.py`, `app/api/v1/test_cases.py`, `app/api/v1/knowledge.py`, `app/services/knowledge_service.py`, and their Vue management pages.
- Purpose: keep management UI actions backed by real API capabilities and prevent masked secrets, fake pagination, or fake vector status from corrupting stored data.

### 2. Signatures

- Test case list:
  ```text
  GET /api/v1/test-cases?page=1&page_size=20&search=checkout&priority=P1&category=API&source=agent
  X-Total-Count: 42
  ```
- Knowledge update:
  ```text
  PUT /api/v1/knowledge/{id}
  {"content": "Updated tester knowledge"}
  ```
- Environment update:
  ```text
  PUT /api/v1/environments/{id}
  {"name": "...", "base_url": "...", "variables": {"TOKEN": "********1234"}, "is_production": false}
  ```

### 3. Contracts

- Test case list must apply `page`, `page_size`, `search`, `priority`, `category`, and `source` server-side and return `X-Total-Count` for the same filters.
- Test case list rows may include safe asset metadata such as `test_data.case_asset`, `source`, `created_at`, `category`, and suite-facing runner metadata. Do not include secret-bearing request headers or raw execution logs.
- Knowledge create and update attempt embedding generation through `knowledge_service`; `embedding_available` must reflect whether `KnowledgeEntry.embedding` is present after the write.
- `PUT /knowledge/{id}` replaces content and regenerates the embedding when an embedding provider is available. If embeddings are unavailable, the update still succeeds and returns `embedding_available=false`.
- Environment list responses return masked variable values only.
- Environment updates must preserve an existing encrypted value when the submitted value equals the current masked display value. This allows editing names/base URLs without replacing a secret with its mask.
- Provider create/update and `/providers/{id}/set-default` must keep at most one active default model per role (`planner`, `coder`, `vision`). Duplicate role defaults can break runtime model lookup and make UI defaults misleading.
- UI actions must not display fake capabilities: do not show arbitrary headers for providers if the backend cannot store them, do not show a global environment run action without a base URL, and do not label knowledge as vector-ready unless `embedding_available=true`.

### 4. Validation & Error Matrix

- `GET /test-cases?page=2&page_size=10&search=x` -> returns only page 2 rows and the full filtered `X-Total-Count`.
- `PUT /knowledge/{missing}` -> `404 Knowledge entry not found`.
- `PUT /knowledge/{id}` with blank content -> `400 content is required`.
- `PUT /knowledge/{id}` when embedding provider is missing -> `200` with updated content and `embedding_available=false`.
- `PUT /environments/{id}` with `variables.TOKEN` equal to the masked value returned by `GET /environments` -> keep the existing encrypted secret.

### 5. Good/Base/Bad Cases

- Good: a case asset table pages through filtered API rows, opens long steps in a detail panel, and uses backend totals for pagination.
- Good: editing a knowledge entry updates the content and reports true vector status based on regenerated embedding.
- Base: no embedding provider is configured; knowledge editing still works and the UI says no vector is available.
- Bad: frontend paginates a full unbounded `/test-cases` response while claiming backend pagination.
- Bad: saving an environment after editing only `base_url` stores `********1234` as the real token.
- Bad: knowledge UI displays "Vector RAG ready" for rows where `embedding_available=false`.

### 6. Tests Required

- Integration: `/test-cases` filters and paginates server-side, returns `X-Total-Count`, and exposes safe asset metadata.
- Integration: `/knowledge/{id}` update changes content and regenerates embeddings when available.
- Regression: knowledge update succeeds without embeddings and reports `embedding_available=false`.
- Regression: environment update preserves existing encrypted values when submitted values match the masked display value.
- Regression: provider create/update with a role default clears conflicting defaults for that role.
- Frontend build/type-check: provider, environment, case asset, and knowledge pages compile against the real API contracts.

### 7. Wrong vs Correct

#### Wrong

```python
result = await db.execute(select(TestCase).order_by(TestCase.created_at.desc()))
return list(result.scalars())
```

#### Correct

```python
total = (await db.execute(count_stmt)).scalar_one()
response.headers["X-Total-Count"] = str(total)
result = await db.execute(stmt.offset(offset).limit(page_size))
return list(result.scalars())
```

#### Wrong

```python
environment.variables_encrypted = {
    key: encrypt_value(value) for key, value in payload.variables.items()
}
```

#### Correct

```python
if mask_secret(decrypt_value(existing_value)) == submitted_value:
    encrypted[key] = existing_value
else:
    encrypted[key] = encrypt_value(submitted_value)
```

## Migrations

- Alembic for migrations: `alembic/versions/`
- Auto-create tables on startup via `Base.metadata.create_all` in lifespan
- Run migrations: `alembic upgrade head`

## Naming Conventions

- Table names: `snake_case` plural (e.g., `tasks`, `test_runs`, `api_documents`)
- Column names: `snake_case` (e.g., `created_at`, `target_url`, `test_type`)
- Primary keys: `id` as `String(36)` UUID
- Timestamps: `created_at`, `updated_at` as `DateTime` with `default=datetime.utcnow`
- Foreign keys: `{referenced_table_singular}_id` (e.g., `task_id`, `api_doc_id`)
- Enums: `str, enum.Enum` subclass (e.g., `TaskStatus`, `TestType`)

## Scenario: Task Execution Progress Contract

### 1. Scope / Trigger

- Trigger: test runs are long-running Celery/LangGraph workflows and must show live progress in the Vue UI through `/api/v1/runs/{id}/stream`.
- Storage source of truth: `Task.execution_log` JSON in the `tasks` table.
- Writers: `app/worker/tasks.py`, `app/agent/progress.py`, and selected agent nodes through `persist_progress(...)`.
- Readers: `app/api/v1/runs.py`, `app/api/v1/tasks.py`, and `frontend/src/pages/RunDetailPage.vue`.

### 2. Signatures

- DB model: `Task.execution_log: Text | None`
- DB status enum: `TaskStatus = pending | queued | running | succeeded | failed | bug_found | cancelled`
- DB test type enum: `TestType = AUTO | UI | API | FUNCTIONAL | FULL | SUITE`
- Progress helper:
  ```python
  await persist_progress(state, node, status, detail, task_status=None)
  await persist_task_state(db, task, state, status=TaskStatus.RUNNING)
  ```
- SSE payload:
  ```json
  {"type": "snapshot", "snapshot": {"workflow_steps": [], "progress_events": []}}
  ```

### 3. Contracts

`execution_log` is a JSON object. Preserve existing keys when appending progress. Known keys:

- `workflow_steps`: coarse agent node steps, each with `node`, `status`, `detail`
- `progress_events`: fine-grained live events, each with `node`, `status`, `detail`, `timestamp`
- `current_step`: latest progress event
- `api_execution_result`, `ui_execution_result`, `final_report`, `artifacts`
- `input_type`, `source_input`, `last_error`
- `cancelled`, `cancelled_at`

API routes must normalize user-facing lowercase test types into DB enum values through `normalize_test_type(...)`, and pass lowercase agent modes through `normalize_agent_test_type(...)`.

### 4. Validation & Error Matrix

- Empty run source -> `400 {"detail": "source is required"}`
- Unsupported `test_type` -> `400` with allowed values
- Unsupported status/test type filter -> `400` with allowed values
- Unknown run/task id -> `404`
- Cancel non-active run/task -> `400`
- SSE without query `token` -> `401`
- Cancelled task detected during persistence -> keep `TaskStatus.CANCELLED` and do not overwrite it with success/failure

### 5. Good/Base/Bad Cases

- Good: worker calls `run_graph_with_progress(...)`, persists a snapshot after every graph update, and SSE emits `snapshot` events while status is `running`.
- Base: synchronous fallback uses the same progress helper path so local dev without Celery still writes compatible `execution_log` JSON.
- Bad: writing `task.execution_log = '{"cancelled": true}'` directly, because it destroys prior workflow steps and logs.

### 6. Tests Required

- Unit: `normalize_test_type("api") == TestType.API`
- Unit: `normalize_agent_test_type(TestType.UI) == "ui"`
- Unit: `determine_final_status({"cancelled": True}) == TaskStatus.CANCELLED`
- Integration: creating a run stores an uppercase DB enum but returns a usable response schema
- Integration: cancelling a queued/running run sets status `cancelled` and preserves previous `execution_log` keys
- Frontend build: `RunDetailPage.vue` must compile after consuming `snapshot`, `progress_events`, and `current_step`

### 7. Wrong vs Correct

#### Wrong

```python
task.status = TaskStatus.FAILED
task.execution_log = '{"cancelled": true}'
await db.commit()
```

#### Correct

```python
await mark_task_cancelled(db, task, "Run cancelled by user")
```

#### Wrong

```python
run_agent_task.delay(task.id, objective, target_url, test_type=payload.test_type)
```

#### Correct

```python
db_test_type = normalize_test_type(payload.test_type, default=TestType.AUTO)
agent_test_type = normalize_agent_test_type(db_test_type, default="auto")
run_agent_task.delay(task.id, objective, target_url, test_type=agent_test_type)
```

## Scenario: Lightweight Run History List and Quality Memory Contract

### 1. Scope / Trigger

- Trigger: `/api/v1/runs` powers the History page list and `/api/v1/runs/insights` powers the Quality Memory page; both must stay fast even when `Task.execution_log` contains large agent traces, screenshots metadata, API results, and final reports.
- Applies to `app/api/v1/runs.py`, `app/schemas/task.py`, History/Quality Memory page rendering, and runs-list/history-insights regression tests.

### 2. Signatures

- List route:
  ```python
  GET /api/v1/runs?page=1&page_size=20&status=failed&test_type=api
  ```
- Quality memory route:
  ```python
  GET /api/v1/runs/insights?days=30&limit=100
  ```
- Response item:
  ```json
  {
    "id": "run-id",
    "target_url": "https://app.example.test",
    "objective": "checkout regression",
    "status": "failed",
    "test_type": "API",
    "created_at": "2026-05-23T09:00:00",
    "updated_at": "2026-05-23T09:01:00",
    "error_message": null
  }
  ```
- Headers: keep `X-Total-Count` for pagination.

### 3. Contracts

- The runs list must select only list columns from `Task`: `id`, `target_url`, `objective`, `status`, `test_type`, `created_at`, and `updated_at`.
- The list response must not include `execution_log`, `generated_code`, `api_doc_id`, `environment_id`, plans, cases, artifacts, tool calls, reports, screenshots, or other detail-only fields.
- Preserve existing pagination and filters: `page`, `page_size`, `status`, and `test_type`.
- Run detail endpoints may continue to load `Task.execution_log`, redact it, and enrich detail summaries.
- Frontend History must not block the run list behind `/api/v1/runs/insights`; list loading and quality-memory loading are separate states.
- The quality-memory insights route must not parse/redact full `execution_log` history for every sampled task. It should fetch sampled task metadata separately and project only the fields needed for triage: count-only `api_execution_result`, count-only `ui_execution_result`, reduced `final_report`, top-level `tool_summary`, top-level `ui_reproducible_script`, and a reduced `artifacts` object.
- The reduced `api_execution_result` must preserve only count fields: `total`, `executed`, `completed`, `passed`, `failed`, `skipped`, and `all_passed`. Do not project `api_execution_result.results`.
- The reduced `ui_execution_result` must preserve only count fields: `total`, `executed`, `completed`, `passed`, `failed`, `skipped`, and `all_passed`. Do not project `ui_execution_result.cases`, `ui_execution_result.commands`, snapshots, stdout, or stderr.
- The reduced `final_report` may include `overall_verdict`, `summary`, `bugs_found`, `recommendations`, `tool_summary`, and reduced `artifacts` with `screenshots` and `tool_summary`.
- The projected `artifacts` object must be limited to evidence summary fields: `ui_screenshots`, `screenshots`, `tool_summary`, and `ui_reproducible_script`. Do not project full `artifacts`, top-level `tool_calls`, `artifacts.tool_calls`, `ui_case_evidence`, `ui_snapshots`, `ui_commands`, or raw `stdout`/`stderr` dumps.
- SQLite must use a bounded JSON projection, and production PostgreSQL must use a sampled CTE/subquery that casts `Task.execution_log` to `JSONB` once as `log_json` after cutoff/order/limit. The outer PostgreSQL projection must build the compact `insight_log` from that `log_json` alias using JSONB extraction (`#>`/getitem-style access) and must not use `jsonb_path_query` or scan/filter result arrays. Unsupported databases may fall back to full parsing.
- A short cache for identical insights requests is allowed when the key includes the query window, sample limit, window row count, and latest task update marker. Do not cache by only `days`/`limit`, because new run completion must invalidate the response.

### 4. Validation & Error Matrix

- Unsupported `status` filter -> `400` with allowed statuses.
- Unsupported `test_type` filter -> `400` with allowed test types.
- Empty result set after filters -> `200 []` with `X-Total-Count: 0`.
- Large `execution_log` rows -> list response excludes the log and still returns list metadata only.
- Large `progress_events`, full `artifacts`, `tool_calls`, UI snapshots, commands, or raw stdout/stderr dumps -> `/runs/insights` ignores those fields and still returns bounded insight summaries without leaking secrets.
- Large passed/skipped/failed API results or UI cases/commands -> `/runs/insights` preserves aggregate counts but does not return result/case/command array items.

### 5. Good/Base/Bad Cases

- Good: `/api/v1/runs?page=1&page_size=20` returns lightweight list rows and `X-Total-Count` without touching detail payload fields.
- Good: `/api/v1/runs/insights?days=30&limit=100` returns trend, evidence, affected target, surface, and recurring-theme summaries from projected log fields only.
- Base: `/api/v1/runs/{id}` still returns redacted `execution_log`, workflow steps, evidence, and triage summaries.
- Bad: serializing `TaskRead` for `/api/v1/runs`, because it includes `execution_log` and can make History load multi-megabyte payloads.
- Bad: parsing and redacting the entire `execution_log` for each Quality Memory row, because verbose progress and command history can dominate page load time.
- Bad: projecting any `api_execution_result.results`, `ui_execution_result.cases`, or `ui_execution_result.commands` array, because result arrays can dominate Quality Memory first load and command output can include huge stdout/stderr dumps.

### 6. Tests Required

- Integration: `/api/v1/runs` response items do not contain `execution_log` or generated/detail fields.
- Integration: status/test type filters and pagination still return the correct rows and `X-Total-Count`.
- Regression: invalid filters still return `400`.
- Regression: `/runs/insights` over large logs with verbose `progress_events`, full `artifacts`, `tool_calls`, UI snapshots, commands, and stdout/stderr dumps does not call the full execution-log parser on SQLite; PostgreSQL uses a sampled `log_json` JSONB CTE/subquery rather than selecting the full `execution_log` row, does not compile `jsonb_path_query(...)`, and omitted secret-bearing history fields do not leak.
- Regression: API results and UI cases/commands are absent from the projected log while count fields remain available.
- Frontend build: `HistoryPage.vue` compiles with independent list and insights loading states.

### 7. Wrong vs Correct

#### Wrong

```python
items, total = await task_service.list(db, page=page, page_size=page_size)
return [TaskRead.model_validate(item).model_dump(mode="json") for item in items]
```

#### Correct

```python
stmt = select(Task.id, Task.target_url, Task.objective, Task.status, Task.test_type, Task.created_at, Task.updated_at)
rows = await db.execute(stmt.order_by(Task.created_at.desc()).offset(offset).limit(page_size))
```

#### Wrong

```python
rows = await db.execute(select(Task).order_by(Task.created_at.desc()).limit(limit))
for task in rows.scalars():
    parsed = redact_sensitive_data(json.loads(task.execution_log or "{}"))
```

#### Correct

```python
projection = func.json_object(
    "api_execution_result",
    func.json_object("total", func.json_extract(Task.execution_log, "$.api_execution_result.total")),
    "artifacts",
    func.json_object("ui_screenshots", func.json_extract(Task.execution_log, "$.artifacts.ui_screenshots")),
)
rows = await db.execute(select(Task.id, Task.target_url, Task.status, Task.created_at, projection).limit(limit))
```

```python
log_json = cast(Task.execution_log, JSONB)
projection = func.jsonb_build_object(
    "api_execution_result",
    func.jsonb_build_object("total", log_json["api_execution_result"]["total"]),
    "artifacts",
    func.jsonb_build_object("ui_screenshots", log_json["artifacts"]["ui_screenshots"]),
)
rows = await db.execute(select(Task.id, Task.target_url, Task.status, Task.created_at, projection).limit(limit))
```

In production PostgreSQL, sample rows first, cast once, and build the projection from the `log_json` alias:

```python
sampled = (
    select(Task.id, Task.target_url, Task.status, Task.created_at, cast(Task.execution_log, JSONB).label("log_json"))
    .where(Task.created_at >= cutoff)
    .order_by(Task.created_at.desc())
    .limit(limit)
    .cte("sampled_history_tasks")
)
log_json = sampled.c.log_json
projection = func.jsonb_build_object(
    "api_execution_result",
    func.jsonb_build_object("total", log_json[("api_execution_result", "total")]),
)
rows = await db.execute(select(sampled.c.id, sampled.c.target_url, sampled.c.status, sampled.c.created_at, projection))
```

## Scenario: UI/API Evidence and Final Report Contract

### 1. Scope / Trigger

- Trigger: agent runs generate browser/API evidence that is persisted into `Task.execution_log` and rendered by the run detail page.
- Applies to `app.agent.nodes.ui_runner`, `app.tools.playwright_commands`, `app.agent.nodes.api_runner`, `app.agent.graph`, and `app.agent.nodes.reporter`.
- Purpose: prevent product reports from reflecting generated script dialect failures or reused screenshot paths instead of actual test execution.

### 2. Signatures

- Command normalizer:
  ```python
  normalize_playwright_commands(commands: list[str], include_unsupported: bool = False) -> list[dict]
  ```
- UI execution result payload:
  ```json
  {
    "total": 2,
    "completed": 2,
    "passed": 2,
    "failed": 0,
    "case_total": 2,
    "command_total": 8,
    "cases": [],
    "commands": [],
    "screenshots": [],
    "normalization_warnings": []
  }
  ```
- Final report payload:
  ```json
  {
    "api_test_summary": {"total": 1, "passed": 1, "failed": 0, "planned_cases": 1},
    "ui_test_summary": {"total": 2, "passed": 2, "failed": 0, "planned_cases": 2},
    "overall_verdict": "PASS"
  }
  ```

### 3. Contracts

- `ui_execution_result.total` is the UI case count. Use `command_total` for command count.
- Every UI case must run independently and write screenshots to a run-scoped, case-scoped path:
  `screenshots/{task_id}/case_{case_index}_step_{step_index}_shot_{n}.png`.
- Do not reuse generated screenshot filenames such as `step1.png`; the runner owns evidence paths.
- Generated pseudo-commands `wait`, `sleep`, `pause`, `assert`, and `expect` must be normalized before execution or skipped without being counted as playwright syntax failures.
- `assert snapshot contains "text"` becomes a `snapshot` execution plus an in-process text check.
- Auto runs must execute API first when an API schema, API cases, or `base_url_override` is available, then continue to UI when a UI URL is available.
- The reporter must build result counts from `api_execution_result` and `ui_execution_result`, not from draft plans or LLM-generated summaries.

### 4. Validation & Error Matrix

- Generated `wait 2000` -> normalize to `snapshot`; no syntax failure.
- Generated `assert snapshot contains "Dashboard"` -> execute `snapshot`, fail only if actual snapshot text is missing `Dashboard`.
- Generated `screenshot shared.png` -> save to the runner-owned case evidence path.
- API schema/base URL available but no API execution -> final report recommendation: verify schema/base URL.
- No API schema/base URL supplied -> API execution is not applicable, not a product failure.

### 5. Good/Base/Bad Cases

- Good: UI case screenshots have distinct paths and the report says `UI passed 3/3` for three passing cases.
- Base: a generated wait command is normalized and recorded in `normalization_warnings`.
- Bad: all UI evidence points to the same file or final report says failures were caused by unsupported generated commands.

### 6. Tests Required

- Unit: command normalizer converts `wait`, `assert snapshot contains`, and `screenshot <name>`.
- Unit: auto graph routing sends URL + `base_url_override` runs through API before UI.
- Async unit: UI runner writes different screenshot paths for different cases.
- Unit: reporter uses actual API/UI execution counts and does not report draft-only API plans as execution.
- Frontend build: run detail page compiles with UI case/count fields.

### 7. Wrong vs Correct

#### Wrong

```python
screenshots.append("step1.png")
ui_result["total"] = len(commands)
```

#### Correct

```python
screenshots.append(str(screenshot_dir / "case_000_step_002_shot_001.png"))
ui_result["total"] = len(case_results)
ui_result["command_total"] = len(command_results)
```

### Scenario: Optional OSS Screenshot Storage

### 1. Scope / Trigger

- Trigger: UI runs persist screenshots as test evidence, and the project may store those screenshots in Aliyun OSS when credentials and bucket settings are present.
- Applies to `app.services.screenshot_storage`, `app.agent.nodes.ui_runner`, `app.config`, and the run detail page that renders screenshot evidence.
- Purpose: keep screenshot evidence available even when OSS is not configured, while exposing a stable remote URL when upload succeeds.

### 2. Signatures

- Screenshot storage helper:
  ```python
  async def store_screenshot(path: Path, run_id: str) -> dict
  ```
- OSS configuration keys:
  ```text
  OSS_ENABLED
  OSS_BUCKET
  OSS_REGION
  OSS_ENDPOINT
  OSS_PUBLIC_BASE_URL
  OSS_PREFIX
  OSS_USE_CNAME
  OSS_ACCESS_KEY_ID
  OSS_ACCESS_KEY_SECRET
  ```
- Screenshot evidence payload:
  ```json
  {
    "path": "sandbox/screenshots/run-1/case_000_step_001_shot_001.png",
    "label": "点击操作后",
    "detail": "click e12",
    "storage": {
      "backend": "oss",
      "bucket": "qunsun",
      "key": "testclaw/screenshots/run-1/case_000_step_001_shot_001.png",
      "url": "https://qunsun.oss-cn-hangzhou.aliyuncs.com/testclaw/screenshots/run-1/case_000_step_001_shot_001.png"
    }
  }
  ```

### 3. Contracts

- Screenshots are written locally first; OSS upload is an optional second step.
- `store_screenshot(...)` must return a structured dict with `backend` set to `local`, `oss`, or `missing`.
- When OSS is configured and upload succeeds, the result should include `bucket`, `key`, `url`, and request metadata such as `etag` / `request_id` when available.
- When OSS is not configured or upload fails, the run must continue and preserve the local `path` so the run detail page can still render evidence.
- The run detail page should prefer `storage.url` or `url` when present, and fall back to the local screenshot route when remote storage is unavailable.

### 4. Validation & Error Matrix

- OSS disabled or missing bucket/region -> return `backend=local`; do not fail the run.
- OSS upload succeeds -> return `backend=oss` with a stable remote URL.
- OSS upload fails -> return `backend=local` with `oss_error`; do not discard the local file path.
- Screenshot file missing on disk -> return `backend=missing`.

### 5. Good/Base/Bad Cases

- Good: the UI result contains both local screenshot paths and OSS URLs when the bucket is configured.
- Base: local-only runs still persist evidence and the detail page can load images through the backend route.
- Bad: a failed OSS upload aborts the UI run or leaves the report without any screenshot path.

### 6. Tests Required

- Unit: `store_screenshot(...)` returns `backend=local` when OSS is not configured.
- Smoke: a configured OSS environment uploads a file and returns `backend=oss` with a non-empty `url`.
- Frontend build: the run detail page can render screenshot evidence with `url`, `storage`, and local fallback fields.

### 7. Wrong vs Correct

#### Wrong

```python
upload_to_oss(path)
return {"url": remote_url}
```

#### Correct

```python
storage = await store_screenshot(path, task_id)
evidence = {
    "path": str(path),
    "label": label,
    "detail": detail,
    "storage": storage,
    "url": storage.get("url"),
}
```

## Scenario: Sensitive Header Redaction and Suite Routing

### 1. Scope / Trigger

- Trigger: API execution results and suite-selected API cases can include user-supplied auth/custom headers.
- Applies to `app.core.redaction`, `app.agent.progress`, `app.agent.nodes.api_runner`, `app.schemas.task`, `app.api.v1.runs`, and `app.api.v1.test_cases`.
- Purpose: prevent credentials from being persisted in `Task.execution_log` or rendered in run detail/SSE payloads.

### 2. Signatures

- Redaction helpers:
  ```python
  redact_sensitive_headers(headers: Any) -> Any
  redact_sensitive_data(value: Any) -> Any
  redact_json_text(text: str | None) -> str | None
  ```
- Suite worker kwargs:
  ```python
  _suite_worker_kwargs(agent_test_type: str, api_cases: list[dict], ui_cases: list[dict]) -> dict
  ```

### 3. Contracts

- Redact `Authorization`, `Proxy-Authorization`, `X-API-Key`, `Api-Key`, and any header name containing `token` or `cookie`, case-insensitively.
- Redaction must happen before writing `Task.execution_log` via `build_execution_log_payload(...)`.
- Run detail and stream readers must redact parsed execution logs again so older unredacted rows are not rendered.
- `api_runner` may send real headers to `httpx`, but stored `request_headers` in `api_execution_result.results[]` must be redacted.
- Suite runs must normalize selected case kind before routing:
  - explicit `API`/`api` -> API
  - explicit `UI`/`ui` and UI labels such as `PAGE_LOAD` -> UI
  - request templates imply API
  - playwright commands imply UI
- Suite API case payloads must hoist `test_data.request_template` to top-level `request_template`.
- Production Celery dispatch for suite runs must pass both `api_cases` and `ui_cases`; synchronous fallback must use the same payloads.

### 4. Validation & Error Matrix

- Sensitive header in API request template -> persisted value is `[REDACTED]`.
- Sensitive header in API execution result -> run detail returns `[REDACTED]`.
- Suite case category `API` with request template -> routed to `api_cases`.
- Suite case category `SMOKE` with request template -> routed to `api_cases`.
- Suite case category `PAGE_LOAD` with playwright commands -> routed to `ui_cases`.
- Suite with no valid case ids -> `400 {"detail": "No valid test cases found in suite"}`.

### 5. Good/Base/Bad Cases

- Good: API request executes with the real `Authorization` header, but persisted/rendered `request_headers.Authorization` is `[REDACTED]`.
- Base: a suite with one API case and one UI case dispatches `test_type="auto"` plus both case lists.
- Bad: Celery `.delay(...)` receives only `source_input="suite"` and regenerates cases, losing selected suite request templates.

### 6. Tests Required

- Unit: imported modules for new agent/progress/playwright files are importable.
- Unit: API runner uses nested suite request templates and redacts stored request headers.
- Unit: `build_execution_log_payload(...)` and `parse_task_detail(...)` redact sensitive headers.
- Unit: suite case payload builder normalizes `API`, `SMOKE`, and `PAGE_LOAD` cases and hoists request templates.
- Unit: suite worker kwargs include both `api_cases` and `ui_cases` for the production delay path.

### 7. Wrong vs Correct

#### Wrong

```python
run_agent_task.delay(task.id, task.objective, target_url, test_type=agent_test_type)
```

#### Correct

```python
run_agent_task.delay(
    task.id,
    task.objective,
    target_url,
    test_type=agent_test_type,
    source_input="suite",
    api_cases=api_cases,
    ui_cases=ui_cases,
)
```

## Scenario: Authenticated UI Login Verification, Modality Gating, and Honest Run Progress

### 1. Scope / Trigger

- Trigger: a run starts from a login page or authenticated admin entry and the agent must decide whether authentication actually succeeded before planning or executing authenticated UI coverage.
- Applies to `app/agent/nodes/ui_login.py`, `app/agent/graph.py`, `app/agent/nodes/ui_test_planner.py`, `app/agent/nodes/ui_runner.py`, `app/agent/nodes/reporter.py`, `app/agent/nodes/planner.py`, `app/agent/nodes/tc_generator.py`, and `frontend/src/pages/RunDetailPage.vue`.
- Why code-spec depth is required: this is a cross-layer contract between agent state, workflow routing, persisted execution snapshots, and the run detail UI. If any layer treats the login page as authenticated success, the product generates false UI coverage, blank reports, and misleading progress.

### 2. Signatures

- Agent state fields:
  ```python
  login_result: dict | None
  login_verified: bool | None
  login_verification_reason: str | None
  authenticated_ui_context: dict | None
  ui_reproducible_script: str | None
  ```
- Graph routing:
  ```python
  def _after_ui_login(state: AgentState) -> str:
      login_required = bool((state.get("login_instructions") or "").strip())
      login_verified = state.get("login_verified")
      if login_required and login_verified is False:
          return "reporter"
      return "ui_test_planner"
  ```
- Reporter verdict contract:
  ```python
  if total_executed == 0:
      verdict = "FAIL" if login_failed else "NOT_EXECUTED"
  ```
- Run detail progress inputs:
  ```json
  {
    "workflow_steps": [],
    "progress_events": [],
    "current_step": {"node": "ui_login", "status": "running", "detail": "..."}
  }
  ```

### 3. Contracts

- `ui_login.py` must preserve the existing LLM-assisted login flow, then verify the post-login snapshot conservatively before treating the session as authenticated.
- Missing `login_instructions` means login is not required for the current run; this is not a failure.
- Required login + `login_verified == False` must short-circuit from `ui_login` to `reporter`; it must not continue to authenticated exploration or UI execution.
- `ui_test_planner.py` may explore the authenticated surface only when login is either not required or has been verified.
- `ui_runner.py` must preserve per-case execution and evidence capture, but when required login is unverified it must emit a clear skipped/failed `ui_execution_result` instead of pretending cases ran.
- `planner.py` and `tc_generator.py` must respect explicit modality selection:
  - `test_type == "ui"` -> no `api_plan`, no `api_cases`, no API entries in combined plan/case payloads.
  - `test_type == "api"` -> no `ui_plan`, no `ui_cases`, no UI entries in combined plan/case payloads.
- `reporter.py` must use executed results plus login verification state to build the final verdict. UI-only runs mark API as not applicable; failed required login produces a non-empty `FAIL` summary and recommendations.
- `RunDetailPage.vue` must derive in-flight progress from `workflow_steps`, `progress_events`, and `current_step`/snapshot data. Running state must not jump near completion before the workflow actually reaches late nodes.

### 4. Validation & Error Matrix

- `login_instructions` provided, post-login snapshot still contains login-form markers, and no authenticated markers -> `login_verified = False`, reporter verdict `FAIL`, authenticated UI planning/execution skipped.
- `login_instructions` missing -> `login_result.required = False`, `ui_login` step is skipped, downstream UI planning may continue from the current page.
- `test_type == "ui"` with API schema present -> API planning/case generation remains empty; report says API not applicable instead of implying execution.
- `test_type == "api"` with URL present -> UI planning/case generation remains empty.
- `workflow_steps` only show early nodes running -> run detail progress stays in an early/mid range rather than inflating to ~95%.

### 5. Good/Base/Bad Cases

- Good: login succeeds, graph continues `ui_login -> ui_test_planner -> ui_runner`, explored admin pages produce UI cases, screenshots, and an actionable reproducible script.
- Base: login is not required, `ui_login` records a skipped step, UI planning starts from the opened page, and report remains meaningful.
- Bad: the agent stays on the login page, still generates authenticated UI cases, and the report/progress imply broad coverage.

### 6. Tests Required

- Unit: graph routing sends failed required login from `ui_login` to `reporter`, and successful/non-required login to `ui_test_planner`.
- Unit: planner/tc_generator enforce UI-only and API-only gating even when both URL and API schema are present.
- Unit/async: `ui_runner` emits a clear skipped/failed result when required login verification fails.
- Unit: reporter marks UI-only API summary as not applicable and failed required login as `FAIL` with non-empty summary/recommendations.
- Frontend build: `RunDetailPage.vue` compiles after consuming snapshot-driven `workflow_steps`, `progress_events`, and `current_step`.

### 7. Wrong vs Correct

#### Wrong

```python
graph.add_edge("ui_login", "ui_test_planner")
```

#### Correct

```python
graph.add_conditional_edges(
    "ui_login",
    _after_ui_login,
    {
        "ui_test_planner": "ui_test_planner",
        "reporter": "reporter",
    },
)
```

#### Wrong

```python
if test_type == "ui":
    api_plan = parsed.get("api_plan")
```

#### Correct

```python
if test_type == "ui":
    api_plan = None
```

#### Wrong

```python
if run.status == "running":
    progress = 95
```

#### Correct

```python
progress = derive_from(workflow_steps, progress_events, current_step)
```

## Scenario: Run URL Roles and Rerun Context Rehydration

### 1. Scope / Trigger

- Trigger: a run may include both a browser page URL and an API base URL override, or may be re-run from a stored `Task.execution_log`.
- Applies to `frontend/src/pages/DocumentsPage.vue`, `app/api/v1/runs.py`, `app/api/v1/test_cases.py`, `app/worker/tasks.py`, `app/agent/nodes/source_loader.py`, `app/agent/nodes/tc_generator.py`, and `app/agent/progress.py`.
- Why code-spec depth is required: document source URLs, page URLs, API base URL overrides, selected suite cases, auth/custom headers, and setup instructions cross the UI -> API -> DB -> worker -> agent boundary. Losing or deriving the wrong field can silently skip UI execution, strip an OpenAPI server path prefix, or rerun a different target.

### 2. Signatures

- Run creation:
  ```python
  RunCreate(source: str, base_url: str | None, headers: dict | None, token: str | None)
  ```
- Documents page handoff:
  ```typescript
  router.push({ path: "/run", query: { source: document.source_url || document.raw_content, test_type: "api" } })
  ```
- Worker dispatch fields:
  ```python
  run_agent_task.delay(
      task_id,
      objective,
      target_url,
      source_input=source,
      ui_seed_url=page_url,
      input_type=input_type,
      base_url_override=api_base_url,
      auth_headers=headers,
      custom_headers=headers,
      api_cases=api_cases,
      ui_cases=ui_cases,
  )
  ```
- Persisted rerun context keys:
  `source_input`, `input_type`, `ui_seed_url`, `base_url_override`, `auth_headers`, `custom_headers`, `api_cases`, `ui_cases`, `setup_instructions`, `login_instructions`.

### 3. Contracts

- For normal `input_type == "url"` page runs, `Task.target_url` remains the user page URL even when `base_url` is supplied. The API base host is stored only as `base_url_override`.
- Documents page `去运行`/`用此文档运行` must pass the original stored OpenAPI/Swagger document URL as `source` when `source_url` exists. If the document is raw-only, pass `raw_content` as `source`.
- Documents page must not derive and pass a root/origin `base_url` from a document URL or from OpenAPI `servers`. Leaving `base_url` empty lets backend source loading preserve document-declared path prefixes such as `/api`.
- `source_loader.py` must not overwrite a URL page `target_url` with `base_url_override`; API runners read `base_url_override` separately.
- Mixed suites with both API and UI cases dispatch as `test_type="auto"`/DB `FULL`, execute API first, then UI.
- Suite dispatch must preserve selected `api_cases` and `ui_cases`; `tc_generator.py` must not replace them with generated cases.
- Suite UI cases must carry a UI seed URL or equivalent `input_type="url"` metadata so `_after_api_runner(...)` can continue to the UI path.
- Rerun must rebuild worker kwargs from `execution_log`, not only from the task row, because the task row does not store selected cases, setup/login instructions, safe custom headers, or URL role metadata.
- Rerun header rehydration must drop sensitive header names and `[REDACTED]` placeholder values. It may preserve non-sensitive plain headers such as `X-Tenant` or `X-Trace-ID`, but must never replay `Authorization`, `Cookie`, `X-API-Key`, token-like headers, or redacted placeholders from stored logs.
- Persisted `source_input` for pasted OpenAPI JSON must remain structurally parseable after redaction. Redact secret-bearing scalar values, but do not corrupt OpenAPI metadata such as `security: [{"Authorization": []}]` or `components.securitySchemes.Authorization`; rerun and preflight depend on parsing that stored source.

### 4. Validation & Error Matrix

- URL page run with `base_url` override -> stored `target_url` is the page URL; worker receives `ui_seed_url=page_url` and `base_url_override=api_base_url`.
- API document card with `source_url=https://wms.qunsun.me/openapi.json` and OpenAPI `servers[0].url=https://wms.qunsun.me/api` -> Run page query includes `source=https://wms.qunsun.me/openapi.json` and omits `base_url`; preflight targets `https://wms.qunsun.me/api`.
- Raw-only OpenAPI document -> Run page query includes raw OpenAPI text as `source` and omits derived `base_url`.
- Swagger/OpenAPI run with `base_url` override -> source loader may set `target_url` to the API base override.
- Mixed suite with API and UI cases -> graph routes `tc_generator -> api_runner -> ui_login`.
- Rerun with stored `api_cases`/`ui_cases` -> new worker task receives those exact cases.
- Missing stored `source_input` but stored `ui_seed_url` -> rerun uses `ui_seed_url` as the source input fallback.
- Rerun with stored `auth_headers.Authorization="[REDACTED]"` or old unredacted sensitive headers -> worker kwargs omit those headers.
- Stored OpenAPI `source_input` with `security: [{"Authorization": []}]` and a redacted auth header -> persisted `source_input` is still valid JSON and can be parsed for endpoints; the runtime auth header value remains redacted.

### 5. Good/Base/Bad Cases

- Good: a user enters `source=https://web/login` and `base_url=https://api`; UI login opens the web page while API tests use the API base.
- Good: imported WMS OpenAPI document handoff sends `source=https://wms.qunsun.me/openapi.json` with no `base_url`, so `/api/login` is inferred from the document server.
- Base: API-only Swagger rerun has no UI seed and executes only API paths.
- Bad: Documents page strips `/openapi.json` to `https://wms.qunsun.me` and sends it as `base_url`, causing auth preflight to call `/login` instead of `/api/login`.
- Bad: `target_url` is replaced with the API base during run creation, so login/planning opens `https://api` instead of the page URL.
- Bad: regex redaction turns an OpenAPI security requirement into `{"Authorization": [REDACTED]}`, making stored `source_input` invalid JSON and breaking rerun/preflight.

### 6. Tests Required

- Unit: run target resolver preserves page URL when `base_url` is present for URL input.
- Frontend regression/build: Documents page run handoff for an imported OpenAPI URL passes the document URL in `source` and does not include a derived `base_url`.
- Unit: `source_loader` keeps URL page `target_url` while preserving `base_url_override`.
- Unit: suite worker kwargs include selected API/UI cases plus UI seed metadata.
- Unit: rerun context rehydrates source, cases, setup/login instructions, base URL override, safe custom headers, UI seed, and input type from stored `execution_log`; sensitive or redacted headers are filtered.
- Unit: execution-log redaction preserves nested OpenAPI source JSON structure, including security requirement arrays and security scheme metadata, while redacting actual auth/header values.
- Unit: tc generator preserves suite-selected cases instead of replacing them.

### 7. Wrong vs Correct

#### Wrong

```typescript
router.push({ path: "/run", query: { source: document.source_url, base_url: originFrom(document.source_url) } })
```

#### Correct

```typescript
router.push({ path: "/run", query: { source: document.source_url || document.raw_content, test_type: "api" } })
```

#### Wrong

```python
if payload.base_url:
    target_url = payload.base_url
```

#### Correct

```python
target_url = source if input_type == "url" else (payload.base_url or source)
```

#### Wrong

```python
run_agent_task.delay(new_task.id, objective, target_url, source_input=source_input)
```

#### Correct

```python
run_agent_task.delay(new_task.id, objective, target_url, **rerun_context_from_execution_log)
```

#### Wrong

```python
headers = json.loads(task.execution_log)["auth_headers"]
run_agent_task.delay(new_task.id, objective, target_url, auth_headers=headers)
```

#### Correct

```python
headers = filter_rehydratable_headers(json.loads(task.execution_log).get("auth_headers"))
run_agent_task.delay(new_task.id, objective, target_url, auth_headers=headers or None)
```

#### Wrong

```python
source_input = redact_sensitive_text(source_input)
# {"security": [{"Authorization": []}]} becomes invalid JSON.
```

#### Correct

```python
payload = redact_sensitive_data({"source_input": source_input})
# JSON structure survives; only secret-bearing scalar values are redacted.
```

## Scenario: Agent-Analyzed UI Execution Context and Bounded Browser Tools

### 1. Scope / Trigger

- Trigger: UI runs start from an entry page plus optional setup instructions, then execute generated or selected Playwright CLI cases.
- Applies to `app/agent/nodes/ui_test_planner.py`, `app/agent/nodes/ui_runner.py`, `app/tools/playwright_tool.py`, `app/agent/tool_registry.py`, `app/agent/progress.py`, and Run Detail tool rendering.
- Why code-spec depth is required: setup state, LLM context analysis, browser command execution, persisted evidence, and UI reporting cross agent, tool, storage, and frontend boundaries.

### 2. Signatures

- Tool registry entry:
  ```python
  "planner.analyze_ui_execution_context"
  ```
- Agent state / execution log field:
  ```python
  ui_execution_context_plan: list[dict] | None
  ```
- Context decision shape:
  ```json
  {
    "case_index": 0,
    "use_prepared_context": true,
    "strip_preparation_steps": true,
    "intent": "prepared_context_flow",
    "reason": "Use verified setup state and remove repeated setup commands",
    "source": "llm"
  }
  ```
- Browser command helper:
  ```python
  async def run_playwright_cli_command(command: str, session: str = "default") -> dict
  ```

### 3. Contracts

- UI runners must analyze each case before execution when verified setup/auth context is available.
- The analysis must use the case payload, setup instructions, post-setup URL, and post-setup snapshot; it must not depend on a hardcoded site, menu, account, URL, or domain-specific text.
- `use_prepared_context=true` means the runner restores browser state and opens the post-setup URL before executing the case.
- `strip_preparation_steps=true` means generated `open entry`, form fill, submit, and setup screenshots are removed so the case starts from the prepared business context.
- Login/setup validation cases must remain fresh-entry cases so negative login, empty credential, forgotten-password, captcha, or unauthorized checks still exercise the entry flow.
- Suite-selected UI cases preserve user intent unless the case explicitly requests prepared context.
- `playwright-cli` subprocess timeouts must kill the subprocess and drain output before returning a timeout result.
- Tool calls must record context analysis and browser execution so Run Detail can show selected skills, tool counts, and per-case tool history.

### 4. Validation & Error Matrix

- Verified setup + generated business case with repeated setup commands -> restore setup state, strip repeated setup, execute business action.
- Verified setup + login failure validation case -> do not restore setup state; execute from entry page.
- Suite-selected UI case without explicit prepared context -> keep original command semantics.
- LLM context analysis unavailable -> fallback to generic metadata-only rules and record a failed `planner.analyze_ui_execution_context` tool call.
- `playwright-cli` command exceeds `PLAYWRIGHT_CLI_TIMEOUT_SECONDS` -> kill process, return `status_code=-1`, and continue/fail the case without leaving orphan node/chrome processes.

### 5. Good/Base/Bad Cases

- Good: a login-page run first verifies setup, then the agent plans cases from the authenticated snapshot, records context decisions, and passes UI cases using browser tools.
- Base: LLM context analysis fails; generic fallback still prevents replaying setup for obvious prepared-context cases and keeps selected suites unchanged.
- Bad: all legacy cases blindly reuse authenticated context, causing login-form refs to be filled on a dashboard page.
- Bad: `asyncio.wait_for(proc.communicate())` times out but does not kill the subprocess, leaving `playwright-cli go-back` or Chrome processes running.

### 6. Tests Required

- Unit: tool registry does not include API chain skills for UI-only setup runs.
- Unit: UI runner keeps login validation cases on the entry page even when setup context exists.
- Unit: UI runner strips repeated setup commands for authenticated business cases.
- Integration: historical UI rerun records `ui_execution_context_plan`, `tool_summary`, screenshots, and succeeds when all executed cases pass.
- Regression: `run_playwright_cli_command` timeout behavior must not leave long-lived command processes.

### 7. Wrong vs Correct

#### Wrong

```python
if authenticated_setup_commands:
    raw_commands = [*authenticated_setup_commands, *_strip_leading_navigation(raw_commands)]
```

#### Correct

```python
context_decisions = await _analyze_ui_execution_context(state, ui_cases)
ui_cases = _apply_ui_execution_context_plan(ui_cases, context_decisions)
```

#### Wrong

```python
stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
```

#### Correct

```python
try:
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
except asyncio.TimeoutError:
    proc.kill()
    stdout, stderr = await proc.communicate()
```

## Scenario: Runtime RAG Knowledge Retrieval Contract

### 1. Scope / Trigger

- Trigger: agent runs should make the knowledge base visibly affect planning instead of leaving RAG as an isolated settings page.
- Applies to `app/agent/graph.py`, `app/agent/nodes/knowledge_retriever.py`, `app/agent/nodes/planner.py`, `app/agent/nodes/tc_generator.py`, `app/agent/progress.py`, run detail parsing, and the Run Detail tools/RAG surface.
- Purpose: vector-retrieve safe historical test knowledge before planning, inject it into LangChain prompts, persist a redacted retrieval summary, and show testers what context influenced the run without mislabeling lexical fallback as vector RAG.

### 2. Signatures

- Graph order:
  ```text
  input_classifier -> source_loader -> knowledge_retriever -> planner -> tc_generator -> ...
  ```
- Agent state fields:
  ```python
  rag_context: str | None
  rag_retrieval: dict | None
  ```
- Knowledge storage:
  ```python
  KnowledgeEntry.embedding: JSON | None  # list[float] generated from redacted content
  DEFAULT_EMBEDDING_MODEL=text-embedding-3-small
  await llm_gateway.get_embeddings(db)
  await embedding_service.embed_document(db, content)
  ```
- Tool capability:
  ```text
  memory.retrieve_rag_context
  ```
- Execution log keys:
  ```text
  rag_context, rag_retrieval
  ```

### 3. Contracts

- `knowledge_service.create(...)` and `knowledge_sink` attempt to generate and store embeddings for new knowledge entries through the configured OpenAI-compatible provider path. Content must be redacted before it is sent to an embedding provider.
- `knowledge_retriever` builds a redacted query from objective, target, source input, input type, test type, and parsed endpoint hints, computes a query embedding, and scores recent `KnowledgeEntry.embedding` vectors with cosine similarity.
- If an embedding provider is available, `knowledge_retriever` may backfill missing entry embeddings for the bounded candidate set before scoring.
- Lexical token overlap is not the primary RAG implementation. It may run only as an explicit fallback with `mode="lexical_fallback"` and `status="fallback_lexical"` when vector retrieval cannot be configured or no usable vectors are available.
- Retrieval runs after source loading so parsed API paths can influence matching, and before planner/case generation so prompts can receive `rag_context`.
- `rag_retrieval` is a JSON object with at least `status`, `mode`, `query`, `match_count`, `vector_source_count`, `fallback_reason`, `sources`, and `effect`. It may include `embedding_backfill_count`.
- `status="matched"` is valid only for vector matches when `mode="vector"`. Fallback must be labeled `status="fallback_lexical"` or `status="unavailable"`.
- `sources[]` may include safe identifiers, score/similarity, retrieval mode, snippet, source run id, and created timestamp. Snippets must be redacted before persistence.
- Prompt templates for planner and case generation must accept `rag_context`; fallback text is required when no relevant knowledge exists.
- Run detail and SSE snapshots may expose `rag_retrieval` and `rag_context` only after existing redaction helpers have processed execution logs.
- Run Detail must derive display labels from `rag_retrieval.mode` and `rag_retrieval.status`. If legacy data has `rag_context` but no `rag_retrieval`, show metadata unavailable/context-only copy; never default that state to Vector RAG.
- Tool/skill surfaces should include `rag-knowledge-retrieval` only when a run has retrieval state or injected context.

### 4. Validation & Error Matrix

- No DB session -> `rag_retrieval.status="skipped"`, graph continues.
- DB session but no entries -> `status="empty"`, `mode="vector"` if the embedding provider is usable; planner receives fallback context.
- Matching vector entries found -> `status="matched"`, `mode="vector"`, `rag_context` contains bounded redacted snippets.
- No OpenAI-compatible embedding provider / empty query vector -> `status="unavailable"` when nothing matches fallback, or `status="fallback_lexical"` when explicit lexical fallback context is used.
- Legacy entries without embeddings -> backfill embeddings when the provider is available; if backfill fails, expose `fallback_reason` and do not claim vector retrieval.
- Retrieval exception -> `status="error"`, graph continues without blocking the run.
- Legacy unredacted knowledge content -> embedding input, persisted snippets, and context contain `[REDACTED]` for credentials and secret-looking values.
- Legacy `rag_context` without `rag_retrieval` -> Run Detail labels the panel as retrieval metadata unavailable/context-only, not Vector RAG.

### 5. Good/Base/Bad Cases

- Good: a checkout regression run vector-matches a prior checkout failure note, planner/case generator receive that note, and Run Detail shows mode, vector source count, source snippets, and similarity scores.
- Base: a new target has no similar vector knowledge; the run still plans from live input and the UI says vector retrieval found no similar prior knowledge.
- Base: no embedding provider is configured; Run Detail says vector retrieval is unavailable or explicitly shows lexical fallback with the reason.
- Base: an older run only has `rag_context`; Run Detail can show the context but labels the retrieval metadata as unavailable.
- Bad: RAG page text claims vector runtime influence while the graph uses only keyword matching.
- Bad: Run Detail uses `rag_context` alone as proof that vector retrieval ran.
- Bad: raw passwords, tokens, cookies, Playwright fill/type values, or auth headers appear in `rag_context`, `rag_retrieval.sources[]`, SSE, or Run Detail.

### 6. Tests Required

- Unit: `knowledge_retriever.run(...)` chooses the highest cosine-similarity knowledge vector and sets `rag_retrieval.status="matched"` with `mode="vector"`.
- Unit: embedding input and retrieved context/snippets redact secret-looking values.
- Unit: embedding-provider unavailability sets `status="fallback_lexical"` or `status="unavailable"` with `fallback_reason` and `vector_source_count=0`.
- Unit/frontend: context-only legacy RAG state renders as metadata unavailable, not Vector RAG or available.
- Unit: skill selection includes `rag-knowledge-retrieval` only when retrieval/context exists.
- Unit: graph imports and compiles with `knowledge_retriever` between source loading and planning.
- Frontend build: Run Page and Run Detail compile with RAG architecture and retrieval surfaces.

### 7. Wrong vs Correct

#### Wrong

```python
graph.add_edge("source_loader", "planner")
scored = lexical_overlap(query, recent_knowledge)
state["rag_retrieval"] = {"status": "matched", "sources": scored}
prompt = PLANNER_PROMPT.format(..., rag_context=context)
```

#### Correct

```python
graph.add_edge("source_loader", "knowledge_retriever")
graph.add_edge("knowledge_retriever", "planner")
query_vector = await embedding_service.embed_query_with_client(client, query)
sources = cosine_similarity(query_vector, stored_knowledge_embeddings)
prompt = PLANNER_PROMPT.format(..., rag_context=state.get("rag_context") or "No relevant prior testing knowledge")
```

## Scenario: Agent Evidence Evaluation and Bounded Replanning Contract

### 1. Scope / Trigger

- Trigger: API/UI runners may finish after a shallow or non-executable attempt, but the testing agent must evaluate evidence quality before reporting.
- Applies to `app/agent/graph.py`, `app/agent/nodes/execution_evaluator.py`, API/UI runners, case generation/planning nodes, progress persistence, run detail parsing, SSE snapshots, and Run Detail evidence surfaces.
- Purpose: insert a bounded planner-model quality gate after API/UI execution so the agent can continue to UI, replan API/UI work, or report with actionable diagnostics instead of stopping after one shallow attempt.

### 2. Signatures

- Graph order:
  ```text
  api_runner -> execution_evaluator -> tc_generator|ui_login|reporter
  ui_runner -> execution_evaluator -> ui_test_planner|reporter
  ```
- Agent state / execution log fields:
  ```python
  evidence_evaluation: dict | None
  agent_evaluations: list[dict] | None
  agent_attempt_history: list[dict] | None
  agent_execution_stage: "api" | "ui" | None
  agent_next_node: str | None
  agent_replan_counts: dict[str, int] | None
  agent_replan_feedback: str | None
  ```
- Tool capability:
  ```text
  planner.evaluate_execution_evidence
  ```
- Config:
  ```text
  AGENT_MAX_REPLAN_ATTEMPTS=2
  ```

### 3. Contracts

- `api_runner` and `ui_runner` set `agent_execution_stage` before returning so the evaluator knows which evidence to inspect.
- `execution_evaluator` must summarize redacted API/UI counts, failure samples, latest tool calls, and replan counts before calling the planner model.
- The evaluator may use the planner model to choose `report`, `continue_to_ui`, `replan_api`, or `replan_ui`, but deterministic guardrails must override premature reporting when evidence is clearly insufficient.
- Replanning is bounded per stage by `AGENT_MAX_REPLAN_ATTEMPTS`; after the limit, the evaluator reports and preserves diagnostics instead of looping.
- `replan_api` clears generated `api_cases`, stores compact attempt history, writes `agent_replan_feedback`, and routes to `tc_generator`.
- `replan_ui` clears generated `ui_cases`, stores compact attempt history, writes `agent_replan_feedback`, and routes to `ui_test_planner`.
- Replanning must not alter user-selected suite semantics; suite-selected UI cases should report diagnostics unless the suite explicitly requests prepared-context behavior elsewhere.
- `tc_generator` and `ui_test_planner` must inject `agent_replan_feedback` and recent attempt summaries into prompts for the next iteration.
- `evidence_evaluation`, `agent_evaluations`, `agent_attempt_history`, and `agent_replan_counts` are persisted through `Task.execution_log`, exposed by run detail/SSE after redaction, and rendered in Run Detail.
- Final reports include `agent_diagnostics` and recommendations derived from the latest evaluation.

### 4. Validation & Error Matrix

- API stage builds zero requests while schema/base URL/cases exist -> `next_action="replan_api"` until the API replan limit is reached.
- API stage has sufficient evidence and a UI target exists -> `next_action="continue_to_ui"`.
- UI stage fails after one shallow command or selector-not-found with snapshot context -> `next_action="replan_ui"` until the UI replan limit is reached.
- UI setup/login verification fails -> report with intervention diagnostics; do not loop on UI replanning.
- Planner model unavailable or invalid JSON -> use guardrail decision, record `model_error`, and continue the graph.
- Replan limit reached -> `next_action="report"` with `sufficient_evidence=false` and diagnostic recommendations.
- Legacy execution logs without evaluation fields -> run detail remains compatible and simply hides the evidence-evaluation panel.

### 5. Good/Base/Bad Cases

- Good: an API-only run with generated cases that produce no executable requests replans from schema, records `planner.evaluate_execution_evidence`, then reports with request evidence or a bounded blocker.
- Good: a UI run that fails on a copied stale selector uses latest snapshot evidence to regenerate UI cases before reporting.
- Base: a full run with passing API evidence routes from `execution_evaluator` to `ui_login`, then evaluates UI evidence after `ui_runner`.
- Base: no model provider exists; deterministic guardrails still prevent obvious shallow stops.
- Bad: `api_runner` or `ui_runner` routes directly to `reporter`, bypassing evidence evaluation.
- Bad: evaluator loops indefinitely or clears suite-selected user cases.
- Bad: raw stdout, request bodies, auth headers, cookies, or Playwright typed values appear in persisted evaluation summaries.

### 6. Tests Required

- Unit: graph includes `api_runner -> execution_evaluator` and `ui_runner -> execution_evaluator`, with bounded route targets.
- Unit: evaluator replans API when zero requests were built from available target context.
- Unit: evaluator routes API to UI when API evidence is sufficient and a UI target exists.
- Unit: evaluator replans UI after a single shallow selector failure with snapshot context.
- Unit: evaluator uses the planner model when available and records `planner.evaluate_execution_evidence`.
- Detail/API regression: run detail exposes evaluation fields from execution logs.
- Frontend build: Run Detail compiles with evidence-evaluation and replan surfaces.

### 7. Wrong vs Correct

#### Wrong

```python
graph.add_edge("api_runner", "reporter")
graph.add_edge("ui_runner", "reporter")
```

#### Correct

```python
graph.add_edge("api_runner", "execution_evaluator")
graph.add_edge("ui_runner", "execution_evaluator")
graph.add_conditional_edges(
    "execution_evaluator",
    route_after_evaluation,
    {"tc_generator": "tc_generator", "ui_test_planner": "ui_test_planner", "ui_login": "ui_login", "reporter": "reporter"},
)
```

## Common Mistakes

- Don't forget `await` on all DB operations
- Don't use `session.expire_on_commit=True` (default) — use `expire_on_commit=False`
- Don't mix sync and async SQLAlchemy patterns
- Don't overwrite `Task.execution_log` directly during cancellation or progress updates; merge through `app.agent.progress` helpers.
- Don't pass UI lowercase test types directly into SQLAlchemy enum columns; normalize them first.
- Don't persist API request headers directly; redact sensitive auth/custom headers before writing or rendering execution logs.
- Don't collapse page URL and API base URL into one field; keep `target_url`/`ui_seed_url` for browser execution and `base_url_override` for API execution.
- Don't hardcode UI execution context from a specific website; use `planner.analyze_ui_execution_context` and current snapshots.
- Don't let timed-out `playwright-cli` subprocesses keep running after returning a timeout result.
- Don't describe RAG as runtime behavior unless `knowledge_retriever` persists `rag_retrieval` and injects `rag_context` before planning.
- Don't describe keyword overlap as vector RAG. If embeddings are unavailable, expose `mode="lexical_fallback"` or `mode="unavailable"` with `fallback_reason`.
- Don't route execution runners directly to `reporter`; use `execution_evaluator` so shallow evidence can trigger bounded replanning first.

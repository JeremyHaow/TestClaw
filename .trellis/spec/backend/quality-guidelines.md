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

## Product Wording: Agent Architecture

- UI may describe ReAct-style behavior when the product exposes tool-call traces, input/output summaries, and execution evidence.
- UI may describe Plan-Executor behavior when the flow is Planner -> generated cases/scripts -> API/UI runners -> reporter.
- UI may describe Multi-Agent only as role-based agent orchestration: Planner/Coder/Vision model defaults plus LangGraph nodes for planning, case generation, execution, reporting, and memory.
- Do not imply autonomous peer-to-peer agent collaboration unless the backend implements that behavior.

## Scenario: Agent Plan Mode Session Contract

### 1. Scope / Trigger

- Trigger: users can chat with a planning layer before creating a TestClaw run.
- Applies to `app/api/v1/agent_plans.py`, `app/services/agent_planning.py`, `app/models/agent_planning.py`, `app/api/v1/runs.py`, and the Plan Mode Vue page.
- Purpose: keep conversational planning separate from execution while preserving the existing run preflight/create/worker dispatch contract.

### 2. Signatures

- DB tables:
  ```text
  agent_planning_sessions(id, user_id, title, status, current_plan, current_run_payload, rejection_reason, executed_run_id, created_at, updated_at)
  agent_planning_messages(id, session_id, role, content, plan_json, created_at)
  ```
- API:
  ```text
  POST /api/v1/agent-plans
  GET /api/v1/agent-plans
  GET /api/v1/agent-plans/{session_id}
  DELETE /api/v1/agent-plans/{session_id}
  POST /api/v1/agent-plans/{session_id}/messages
  POST /api/v1/agent-plans/{session_id}/messages/stream
  PUT /api/v1/agent-plans/{session_id}/messages/{message_id}
  PUT /api/v1/agent-plans/{session_id}/messages/{message_id}/stream
  DELETE /api/v1/agent-plans/{session_id}/messages/{message_id}
  POST /api/v1/agent-plans/{session_id}/reject
  POST /api/v1/agent-plans/{session_id}/execute
  ```
- Planner JSON contract:
  ```json
  {
    "response": "...",
    "status": "collecting|ready",
    "questions": [],
    "question_options": [
      {
        "question": "...",
        "step": "target_kind|coverage_scope|auth_boundary|safety_boundary|success_criteria",
        "required": true,
        "options": [
          {
            "label": "...",
            "title": "...",
            "description": "...",
            "field": "target_kind|coverage_scope|auth_boundary|safety_boundary|success_criteria",
            "value": "api_openapi|web_page|smoke|regression|no_login|...",
            "message": "结构化摘要，供继续时发送给 planner",
            "allows_defer": true,
            "allows_skip": false,
            "optional": false
          }
        ]
      }
    ],
    "ready_to_execute": false,
    "plan": {},
    "run_payload": {}
  }
  ```
- Planner stream contract:
  ```text
  event: process           data: {"code": "analyzing_requirement|checking_missing_info|normalizing_target|preparing_plan|waiting_for_confirmation", "label": "...", "status": "..."}
  event: token             data: {"delta": "..."}
  event: final             data: {"session": <redacted session payload>, "process_events": [...]}
  event: error             data: {"detail": "..."}
  ```

### 3. Contracts

- Planner LLM output must be strict JSON only. Local code must tolerate invalid/unavailable LLM output with a deterministic fallback.
- Planner LLM calls in Plan Mode must be bounded by `AGENT_PLAN_LLM_TIMEOUT_SECONDS`; timeout must use the same deterministic fallback path as invalid/unavailable model output.
- `run_payload` may contain only the fields accepted by run creation: `source`, `test_type`, `objective`, `base_url`, `auth_mode`, `captcha_mode`, `auth_credentials`, `auth_config`, `token`, `headers`, `api_execution_policy`, `allow_out_of_schema_api_cases`, and `setup_instructions`.
- Local normalization, not the model, owns allowed values for `test_type`, auth/captcha modes, API execution policy, and secret extraction.
- Local fallback extraction must be generic and multilingual enough for normal Chinese/English tester messages: Chinese labels such as username/account/password/captcha, API/UI intent, no-auth phrases, captcha mode phrases, and safe/write policy phrases should normalize into the same `run_payload` contract as English messages.
- When several user messages contain sources, the latest user-provided source wins over older sources and over stale structured LLM `run_payload.source` values. Rejection/regeneration must not keep the first target forever.
- After rejection, generated `objective`, `setup_instructions`, credentials, and tokens must be derived from the active user messages after the rejection boundary, not from the full rejected conversation history.
- Fallback planning must ask for missing target/source and may produce a safe basic plan when enough generic target information exists. Do not add product-specific target branches.
- Plan rejection clears `current_plan` and `current_run_payload`, records a rejection reason, and leaves the session open for later user messages to regenerate a plan.
- Deleting a planning session must delete its planning messages and return `204`.
- Deleting a planning message is a rollback operation: remove that message and all later messages, clear stale `current_plan`/`current_run_payload`, and restore executable state only if the remaining final message is an assistant message with a ready plan.
- Editing a user message is rollback plus regeneration: update the selected user message, remove all later messages, clear stale executable state, then generate the next assistant turn from the edited conversation. Non-user messages must not be edited.
- Executed planning sessions are immutable chat transcripts: adding, editing, deleting, or streaming a message after execution must return `400 Executed plan cannot be changed` so the session cannot lose `executed` status or stale run linkage.
- Streaming message and edit endpoints must use the same persistence path as non-streaming message and edit endpoints. They may progressively chunk a completed assistant response until true model token streaming is available, but the final event must contain the normalized/redacted session payload.
- Process events are visible operational summaries only (`analyzing_requirement`, `checking_missing_info`, `normalizing_target`, `preparing_plan`, `waiting_for_confirmation`). Do not expose hidden chain-of-thought.
- Clarifying questions may include selectable `question_options`. Each entry is a generic elicitation object with `question`, optional `step`/`required`, and structured `options[]` fields such as `label`, `title`, `description`, `field`, `value`, and `message`. Option messages must represent concrete reusable testing decisions such as supported target type, scope, auth/login boundary, safety boundary, or success criteria. Do not create product-specific option branches.
- Valid `step`/`field` values are `target_kind`, `coverage_scope`, `auth_boundary`, `safety_boundary`, and `success_criteria`. The frontend stepper and backend sanitizer must treat aliases such as `target`, `source`, `scope`, `auth`, `login`, `policy`, and `criteria` as aliases for those canonical values.
- Plan Mode options are structured intake controls, not chat text chips. Selecting an option updates local collected draft state; it must not immediately send a fake user message or dump placeholder text into the chat input. The user explicitly submits with `继续`, `稍后补充`, `跳过`, or the bottom free-form input.
- Frontend structured intake navigation must be driven only by server-confirmed state (`current_step`, `structured_intake`, `current_plan`, or `current_run_payload`). Local option selection, supplemental textarea text, and optimistic skip/defer flags may appear as draft text in the plan draft panel, but must not cause `currentStepId`, `firstOpenStepId`, or the stepper to treat a step as complete before a successful intake response.
- `稍后补充` records a field as intentionally deferred and must not masquerade as a target URL, credential, or answer. `跳过` is available only for optional/non-blocking steps.
- TestClaw Plan Mode supports only API testing and browser-based Web UI testing. Local normalization must filter or replace planner options for unsupported target types such as desktop software, native apps, mobile apps, iOS apps, or Android apps before API/SSE payloads reach the frontend.
- Frontend deterministic target detection must not classify a plain `http(s)` URL as Web UI by URL shape alone. OpenAPI/Swagger/API-doc URLs or schema text classify as API; explicit UI/page/browser markers classify as Web UI; direct URL text with API response semantics such as `响应`, `JSON`, `状态码`, `字段`, `header`, `body`, `接口`, `endpoint`, or `断言` classifies as API. Plain direct URLs stay ambiguous, and ambiguous target-kind choices must include the API option.
- Each structured intake step must include a free-form supplemental path, usually a textarea, so users are not limited to presets.
- Placeholder option messages such as `稍后补充具体地址`, `我会直接粘贴目标 URL`, or `我会补充关于...具体说明` are invalid. Sanitization must drop them before storing/returning `question_options`; target-kind groups left empty after filtering should fall back to supported API/Web UI/custom choices.
- Fallback collecting turns should expose only the highest-priority current option group. The frontend should render only the latest assistant message's first one or two high-quality option groups to avoid flooding the chat.
- Editing an older user message is an immediate UI rollback boundary: later messages and stale plan state must disappear locally as soon as editing starts, before the backend regeneration completes.
- Plan execution must call the existing run creation path or shared lower-level run functions. It must not duplicate auth preflight, task creation, Celery dispatch, or synchronous fallback behavior.
- API responses and stream final payloads must redact tokens, passwords, cookies, captcha/MFA/OTP values, sessions, API keys, auth headers, setup text, and question option messages before returning sessions/messages/plans.

### 4. Validation & Error Matrix

- Missing/unknown session id or wrong user -> `404 Planning session not found`.
- Blank chat message -> request validation error.
- Whitespace-only chat message -> `400 content is required` and no empty `AgentPlanningMessage` row is stored.
- Message without target/source -> `status="collecting"`, `ready_to_execute=false`, and a concrete question.
- UI target without credentials or explicit no-login confirmation -> `status="collecting"` and asks whether login is required.
- LLM unavailable or invalid JSON -> fallback response; no 500 from the planning turn.
- Planner LLM timeout -> fallback response; no hanging HTTP/SSE request.
- Reject with no current plan -> `400 No current plan to reject`.
- Delete/edit unknown message -> `404 Planning message not found`.
- Edit assistant/system message -> `400 Only user messages can be edited`.
- Add/delete/edit message after execution -> `400 Executed plan cannot be changed`.
- Execute without `current_run_payload` -> `400 No executable plan is ready`.
- Existing `/runs` preflight blocks execution -> propagate the run creation `HTTPException` so the UI can show the blocker.
- Secret-bearing user messages or run payloads -> serialized responses contain `[REDACTED]`, not raw values.
- Secret-bearing question option messages -> serialized responses and SSE final payloads contain `[REDACTED]`, not raw values.
- Unsupported target options or placeholder option messages from the model -> sanitize them out before persistence/serialization; if the target group becomes empty, replace it with supported API/Web UI/custom choices.
- Target supplemental text `https://httpbin.org/get 响应需要包含 url 字段` -> deterministic target choices include `API / OpenAPI`; plain `https://example.com` -> choices are not UI-only; `https://example.com 页面` -> may be Web UI.
- Required structured intake step with no selected option or supplemental text -> frontend keeps `继续` disabled; optional/non-blocking step may expose `跳过`.

### 5. Good/Base/Bad Cases

- Good: user describes a Swagger URL and objective, receives a plan card, approves it, and `/agent-plans/{id}/execute` creates the run through `/runs` behavior.
- Good: user rejects a UI plan, types "use API read-only checks instead", and the regenerated payload uses the later target/mode.
- Good: user edits an earlier target message, later messages disappear, stale plan/run payload is cleared, and a regenerated assistant turn reflects the edited target.
- Good: planner asks for auth boundary and returns selectable generic `question_options` such as no login, provide account, manual token, or custom clarification; clicking an option selects the card locally, updates the plan draft summary, and waits for explicit `继续` before sending a structured summary.
- Good: user edits an older message and the UI immediately shows only messages up to that rollback point while waiting for the user to send the revised prompt.
- Good: streaming planner turn emits process events, token events, and a final redacted session payload without requiring a page refresh.
- Good: user describes a public UI target and explicitly says no login is required, receives a ready UI plan.
- Good: user writes `请测试管理后台页面 ... 用户名 ... 密码 ... 固定验证码 ...` and fallback extraction normalizes UI mode, auto auth credentials, static captcha, and the requested API policy without product-specific branches.
- Base: no Planner provider is configured; fallback asks for a target or creates a safe basic plan from a URL/OpenAPI source.
- Base: Planner provider is slow or unavailable; fallback returns within the configured timeout.
- Base: user gives only a UI URL and objective; fallback asks for login boundary instead of surfacing an executable plan that preflight will immediately block.
- Bad: executing a plan manually creates `Task` rows and dispatches workers from the planning route, bypassing auth preflight.
- Bad: plan/session/list responses echo raw `token=...`, `password=...`, `Cookie`, `Authorization`, captcha, session, or API-key values.
- Bad: clicking a choice immediately sends a planner message without letting the user combine options or add text.
- Bad: choices append canned text such as `我要测试网页 UI，稍后补充具体地址` or `我会补充关于...具体说明` into the bottom chat input.
- Bad: question options offer unsupported target types such as desktop software, mobile app, iOS app, Android app, or native app.
- Bad: editing a previous user message leaves later assistant/user messages or stale executable payloads visible in the UI.
- Bad: SSE streams hidden chain-of-thought or omits the final normalized session payload.

### 6. Tests Required

- Integration: create/list/get planning session with current user's sessions only.
- Integration: adding a message with missing target returns a collecting response and question.
- Integration: enough target information returns `ready_to_execute=true`, `current_plan`, and redacted `current_run_payload`.
- Regression: reject clears executable state, records the reason, and a later message regenerates a plan from later instructions.
- Regression: delete planning session removes its messages and hides it from list/get.
- Regression: delete planning message rolls conversation back and clears stale executable state.
- Regression: edit prior user message removes later messages and regenerates from the edited conversation.
- Regression: executed planning sessions reject later message add/edit/delete requests without changing `executed_run_id`.
- Regression: streaming planner message returns `text/event-stream`, process events, token events, and final redacted session payload.
- Regression: planner output and fallback collecting turns expose generic selectable `question_options`, include a free-form choice, filter unsupported target types, and keep fallback to the highest-priority group.
- Regression: backend sanitization removes placeholder option messages and serializes canonical `step`/`field` metadata for planner-provided and fallback `question_options`.
- Regression: frontend source renders stepper/card intake controls, selection state, supplemental textarea, and `跳过`/`稍后补充`/`继续` actions without using the old text-chip draft append behavior; editing an old message immediately hides later messages through a rollback snapshot.
- Regression: frontend source keeps local structured intake drafts out of navigation completion logic; textarea input or local choice selection must not advance from `target_kind` to `coverage_scope` until `submitStructuredIntake()` succeeds, applies the returned server session state, and clears the completed step's local draft.
- Regression: frontend source target detection treats direct URLs with API response wording as API and plain direct URLs as ambiguous/not UI-only.
- Regression: slow planner LLM calls time out and fall back without hanging the planning request.
- Execute path: monkeypatch or otherwise isolate run creation/preflight and assert planning execution delegates to the existing run creation path.
- Frontend build: Plan Mode route and navigation compile with the session/message/plan response shape.

### 7. Wrong vs Correct

#### Wrong

```python
task = Task(objective=payload["objective"], target_url=payload["source"])
db.add(task)
run_agent_task.delay(task.id, ...)
```

#### Correct

```python
from app.api.v1.runs import RunCreate, create_run

task = await create_run(RunCreate(**run_payload), db, user)
```

#### Wrong

```json
{"label": "网页界面", "message": "我要测试网页 UI，稍后补充具体地址。"}
```

#### Correct

```json
{
  "label": "Web UI / 网页",
  "title": "Web UI 页面",
  "description": "用于浏览器页面、登录后业务流程、表单和页面可用性检查。",
  "field": "target_kind",
  "value": "web_page",
  "step": "target_kind",
  "message": "测试目标类型：浏览器 Web UI 页面。"
}
```

## Scenario: Mission Plan Agent Orchestration and Vector Memory Boundary

### 1. Scope / Trigger

- Trigger: TestClaw agent runs must behave like a mission-level AI agent instead of a legacy fixed automation route.
- Applies to `app/agent/graph.py`, `app/agent/nodes/mission_planner.py`, `app/agent/tool_registry.py`, `app/agent/progress.py`, `app/agent/nodes/knowledge_retriever.py`, `app/services/vector_store.py`, run detail/SSE payloads, and related tests.
- Purpose: keep planning, role delegation, ReAct-style traces, and RAG vector storage explicit and testable.

### 2. Signatures

- Graph path:
  ```text
  input_classifier -> source_loader -> mission_planner -> knowledge_retriever -> planner -> agent_supervisor -> tc_generator -> api_runner/ui_login -> execution_evaluator -> reporter -> knowledge_sink
  ```
- Persisted execution-log keys:
  ```python
  agent_mission_plan: dict | None
  agent_roster: list[dict] | None
  agent_delegation_trace: list[dict] | None
  agent_react_trace: list[dict] | None
  ```
- Vector backend configuration:
  ```text
  RAG_VECTOR_STORE_BACKEND=database|milvus
  MILVUS_URI=
  MILVUS_TOKEN=
  MILVUS_COLLECTION=testclaw_knowledge
  MILVUS_DIMENSION=384
  ```

### 3. Contracts

- Active graph wiring must not register or route through legacy `coder`, `executor`, `analyzer`, or `healer` nodes.
- `mission_planner` runs before memory retrieval and persists a real `agent_mission_plan` containing subgoals, memory needs, environment needs, selected skills, execution order, and success criteria.
- `agent_supervisor` runs after planner strategy selection and before fixed case generation. It may add validated skill/tool observations, auth discovery, memory retrieval, and human-input blockers, but it must continue through the existing deterministic execution path until a full supervisor loop replaces it.
- Downstream planner, case generator, and evidence evaluator prompts must consume the mission plan as bounded context.
- Multi-agent collaboration is role-based and persisted through `agent_roster` and `agent_delegation_trace`; expected roles include supervisor/planner, memory researcher, API executor, UI explorer, evidence evaluator, and reporter when relevant to the run.
- ReAct-style trace fields are visible operational summaries only: concise `reason`, `action`, selected `tool`, `observation`, `evidence`, and `next_decision`. Do not store hidden chain-of-thought.
- Runtime RAG must call the vector-store boundary instead of scoring `KnowledgeEntry` rows directly inside agent nodes.
- Default vector backend remains database JSON embeddings. Selecting `milvus` must not require a running Milvus service or installed client for tests; it may fall back to database retrieval with explicit backend metadata.

### 4. Validation & Error Matrix

- Missing DB session during memory retrieval -> `rag_retrieval.mode="skipped"` and mission continues.
- Embedding provider failure -> local embedding fallback where possible; otherwise lexical fallback with `fallback_reason`.
- `RAG_VECTOR_STORE_BACKEND=milvus` without `pymilvus` or `MILVUS_URI` -> database fallback, `requested_backend="milvus"`, and non-secret `backend_config.fallback_reason`.
- Legacy graph node appears in active graph nodes/edges -> failing regression test.
- Tool call includes secret-bearing inputs -> `tool_calls` and `agent_react_trace` must be redacted before persistence.

### 5. Good/Base/Bad Cases

- Good: a full run stores a mission plan, role roster, delegation trace, tool calls, ReAct trace, vector RAG metadata, evidence evaluation, and report diagnostics.
- Base: no memory entries exist; vector retrieval returns `mode="vector"`, `status="empty"`, and planning continues from live input.
- Bad: `tc_generator` routes to `coder`, or graph registers legacy script-generation nodes as active execution paths.
- Bad: prompts receive raw unbounded logs instead of compact mission, memory, tool, and evidence summaries.

### 6. Tests Required

- Unit: complex objective decomposes into multiple mission subgoals with active role delegation.
- Unit: active graph nodes/edges exclude `coder`, `executor`, `analyzer`, and `healer`.
- Unit: active graph routes `planner -> agent_supervisor -> tc_generator`.
- Unit: `build_execution_log_payload(...)` preserves `agent_mission_plan`, `agent_roster`, `agent_delegation_trace`, and `agent_react_trace`.
- Unit: default vector backend selects database storage.
- Unit: Milvus backend selection exposes config metadata without requiring a runtime dependency.
- Regression: existing API scope guardrails still reject out-of-schema generated cases and preserve safe execution policy.
- Frontend build: Run Detail compiles after receiving the new snapshot keys.

### 7. Wrong vs Correct

#### Wrong

```python
graph.add_node("coder", coder.run)
graph.add_edge("tc_generator", "coder")
```

#### Correct

```python
graph.add_node("mission_planner", mission_planner.run)
graph.add_edge("source_loader", "mission_planner")
graph.add_edge("mission_planner", "knowledge_retriever")
```

#### Wrong

```python
entries = await db.execute(select(KnowledgeEntry))
sources = local_cosine_sort(entries, query_vector)
```

#### Correct

```python
vector_store = get_knowledge_vector_store()
entries = await vector_store.load_recent_entries(db, limit)
retrieval = await vector_store.similarity_search(...)
```

## Scenario: Vector RAG Embedding Fallback

### 1. Scope / Trigger

- Trigger: runtime RAG retrieval or knowledge CRUD needs embeddings, but the configured OpenAI-compatible provider is missing or its embeddings endpoint fails.
- Applies to `app/services/embedding_service.py`, `app/services/knowledge_service.py`, and `app/agent/nodes/knowledge_retriever.py`.
- Purpose: keep RAG as vector retrieval even when an external embedding provider returns errors such as `404 page not found`.

### 2. Signatures

- `EmbeddingService.get_client(db) -> Embeddings` must return an embeddings client. It may return a deterministic local fallback client.
- `EmbeddingService.embed_query_with_client(client, text) -> list[float]` returns a non-empty vector or raises `EmbeddingUnavailableError` only when no text/vector can be produced.
- `EmbeddingService.embed_documents_with_client(client, texts) -> list[list[float] | None]` preserves input order and returns one slot per input text.

### 3. Contracts

- External embeddings are preferred when available.
- If provider discovery or provider calls fail, use deterministic local hash embeddings instead of downgrading runtime RAG to keyword search.
- Knowledge create/update should store an embedding whenever either external or local embedding can produce one.
- Runtime retriever should report `mode="vector"` when vectors were used, including local fallback vectors.
- Logs may mention provider failure class/message, but must not include raw knowledge content or secrets.

### 4. Validation & Error Matrix

- No active embedding provider -> local vector fallback, not request failure.
- Provider endpoint returns 4xx/5xx -> local vector fallback, not lexical-only fallback.
- Empty query text -> `EmbeddingUnavailableError` and runtime may continue without RAG.
- Provider returns wrong vector count -> `EmbeddingUnavailableError` unless fallback was already used.

### 5. Good/Base/Bad Cases

- Good: existing knowledge entries without embeddings are backfilled with local vectors during retrieval, then cosine similarity runs over those vectors.
- Base: no similar knowledge is found; retriever returns `status="empty"`, `mode="vector"`, and a positive `vector_source_count`.
- Bad: provider `404 page not found` causes `mode="unavailable"` while knowledge entries exist.
- Bad: fallback stores raw secret-bearing text in logs or response payloads.

### 6. Tests Required

- Unit: provider embedding call failure returns stable local vectors with matching query/document dimensions.
- Unit: runtime retriever uses stored vectors and redacts sensitive context.
- Regression: unavailable provider does not break knowledge create/update flows.

### 7. Wrong vs Correct

#### Wrong

```python
try:
    client = await llm_gateway.get_embeddings(db)
except Exception:
    raise EmbeddingUnavailableError("No embeddings")
```

#### Correct

```python
try:
    return await llm_gateway.get_embeddings(db)
except Exception:
    return self.get_local_client()
```

## Scenario: Model-Driven Agent Strategy Contract

### 1. Scope / Trigger

- Trigger: API/UI agent runs need model-selected strategy and tool plans without adding user-facing special-case options or executable free text.
- Applies to `app/agent/strategy.py`, `app/agent/prompts.py`, `app/agent/nodes/planner.py`, `app/agent/nodes/tc_generator.py`, `app/agent/nodes/api_runner.py`, `app/agent/nodes/execution_evaluator.py`, progress persistence, and run detail surfaces.
- Purpose: let the planner model decide "how to test" while local code enforces schema, method, policy, tool-name, and evidence guardrails.

### 2. Signatures

- Strategy state keys:
  ```python
  state["agent_strategy_decision"]: dict
  state["agent_tool_plan"]: list[dict]
  state["agent_strategy_diagnostics"]: list[dict]
  state["agent_actions"]: list[dict]
  state["agent_action_observations"]: list[dict]
  state["agent_action_diagnostics"]: list[dict]
  ```
- Action runtime:
  ```python
  validate_agent_action_plan(tool_plan, parsed_api_schema, execution_policy) -> list[dict]
  validate_and_record_agent_action_plan(state, stage, strategy, parsed_api_schema, execution_policy) -> list[dict]
  ```
- Normalizer:
  ```python
  normalize_agent_strategy_decision(raw, parsed_api_schema, execution_policy, test_type) -> dict
  fallback_agent_strategy_decision(objective, parsed_api_schema, execution_policy, test_type) -> dict
  ```
- Required JSON fields:
  ```json
  {
    "intent": "api_contract|api_read_only_coverage|api_focused_endpoints|ui_exploration|full_flow|blocked",
    "coverage_scope": "all_documented_safe_methods|focused_documented_endpoints|sampled_contract|ui_paths|none",
    "method_policy": {"allowed_methods": ["GET"], "blocked_methods": ["POST"], "write_allowed": false},
    "endpoint_selection": {"source": "schema|suite|memory|model_focus|fallback", "include": [], "exclude": [], "budget_behavior": "cover_all_within_budget|sample_representative|focused_only"},
    "tool_plan": [{"tool_name": "api.derive_schema_requests", "inputs": {}, "safety_constraints": [], "expected_observation": "..."}],
    "confidence": "low|medium|high",
    "reason": "observable short reason",
    "diagnostics": []
  }
  ```

### 3. Contracts

- Planner prompts must require strict JSON only; no Markdown, hidden chain-of-thought, invented paths, or prose tool instructions.
- The model may choose strategy, coverage scope, endpoint include/exclude lists, and tool plan. It may not grant itself write permission, invent schema paths, or introduce unknown tools.
- Local normalizers must convert invalid model output into `agent_strategy_diagnostics`; the action runtime must then convert `tool_plan` steps into validated `agent_actions`, enrich them with registry risk/policy metadata, record `agent_action_observations`, and block invalid paths/methods/tools before execution.
- Under `safe_read_only` and `safe_with_auth`, POST/PUT/PATCH/DELETE remain blocked even if the model sets `write_allowed=true`.
- Schema-backed API execution must execute only documented method+path pairs selected by the validated strategy or derived from documented safe methods.
- `objective_requests_all_safe_get_coverage(...)` is a fallback only when the planner strategy is missing/unavailable, not the primary strategy mechanism.
- `execution_evaluator` must treat completed `all_documented_safe_methods`, `focused_documented_endpoints`, and `sampled_contract` coverage as reportable evidence even if generated case counts are small.

### 4. Validation & Error Matrix

- LLM returns non-object/invalid JSON -> fallback strategy with diagnostic; run continues within old safe fallback behavior.
- LLM requests unknown `intent`, `coverage_scope`, `tool_name`, endpoint source, or budget behavior -> normalize to a safe default and record a diagnostic.
- LLM includes POST/PUT/PATCH/DELETE under read-only policy -> drop endpoint, force `write_allowed=false`, record `method_blocked_by_policy`.
- LLM includes schema-missing method/path -> drop endpoint, record `out_of_schema_endpoint`.
- LLM emits an unknown tool action -> action is `allowed=false`, records `unknown_tool_name`, and is not executed.
- Valid focused/sample strategy with no surviving include endpoints -> no schema-wide fallback execution; record missing selection and let evaluator/report surface the blocker.

### 5. Good/Base/Bad Cases

- Good: model selects `all_documented_safe_methods`; runner derives GET/HEAD/OPTIONS from OpenAPI and records budget omissions.
- Good: model selects `focused_documented_endpoints`; runner executes only validated include paths and does not force full schema coverage.
- Good: `agent_react_trace` shows concise action reason, selected tool, validated inputs, and observation/diagnostic without storing hidden chain-of-thought.
- Base: model unavailable and objective explicitly asks for all GET requests; fallback derives documented safe method coverage.
- Bad: adding a UI option or keyword branch for "all GET coverage" instead of relying on the strategy contract.
- Bad: treating model-selected POST as safe because it appeared in `method_policy.allowed_methods`.

### 6. Tests Required

- Unit/integration: model strategy `all_documented_safe_methods` drives schema-derived safe coverage without matching objective regex text.
- Unit/integration: focused/sample strategy executes only validated included endpoints.
- Regression: unavailable LLM still handles explicit "test all GET requests" fallback.
- Regression: read-only policy drops model-selected write methods and out-of-schema paths.
- Regression: generic `AgentAction` validation blocks unknown tools, unsafe methods, and out-of-schema paths while preserving redacted observations.
- Regression: evaluator reports completed model strategy scope instead of repeated replanning.

### 7. Wrong vs Correct

#### Wrong

```python
if objective_requests_all_safe_get_coverage(objective):
    execute_all_get_endpoints()
```

#### Correct

```python
strategy = normalize_agent_strategy_decision(raw_model_json, parsed_api_schema, execution_policy, test_type)
if strategy["coverage_scope"] == "all_documented_safe_methods":
    execute_validated_schema_safe_methods(strategy)
```

## Scenario: Run Auth Preflight and Captcha Modes

### 1. Scope / Trigger

- Trigger: run creation must no longer rely on implicit "auto" orchestration or unauthenticated protected API execution.
- Applies to `app/api/v1/runs.py`, `app/services/api_auth.py`, `app/agent/nodes/ui_login.py`, `app/agent/progress.py`, `app/agent/state.py`, and `frontend/src/pages/RunPage.vue`.
- Purpose: make auth readiness explicit for API and UI runs, keep captcha handling mode-specific, and prevent launching tests until protected access or no-auth access is proven.

### 2. Signatures

- New run payload:
  ```python
  RunCreate(
      test_type: "api" | "ui",  # "auto" is history/list compatibility only
      auth_mode: "auto" | "manual" | "none_confirmed",
      captcha_mode: "none" | "static" | "dynamic",
      auth_credentials: {"username": str | None, "password": str | None, "captcha": str | None} | None,
      auth_preflight_id: str | None,
  )
  ```
- Preflight response:
  ```json
  {
    "auth_preflight": {
      "auth_preflight_id": "...",
      "auth_mode": "auto",
      "captcha_mode": "dynamic",
      "status": "passed|blocked|warning",
      "strategy": "auto_login|manual_header|none_confirmed|ui_browser_login",
      "steps": [],
      "missing_fields": [],
      "validation_results": [],
      "can_start": true
    }
  }
  ```
- API captcha helper:
  ```python
  await fetch_captcha_context(config, source=source, input_type=input_type, target_url=target_url, endpoints=endpoints)
  ```

### 3. Contracts

- `POST /runs` and `/runs/preflight` accept new runs only with `test_type="api"` or `"ui"`; existing stored `AUTO` runs and list/detail filters remain supported.
- `auth_mode="auto"` requires usable login credentials and may infer login/token/captcha/csrf endpoints from OpenAPI. Execution is limited to auth-related endpoints plus read-only verification endpoints.
- `auth_mode="manual"` must still validate the supplied Token/Header against protected read-only endpoints before launch.
- `auth_mode="none_confirmed"` must call read-only endpoints without auth and may launch only when no-auth access is verified.
- API dynamic captcha fetches captcha context only. It may use returned `uuid`, `captchaKey`, session cookie names, csrf/xsrf header names, `img`, and `captchaEnabled`; it must not call OCR/Vision. If login requires captcha text and the API does not return clear text, preflight is blocked and asks for static captcha.
- UI dynamic captcha requires a default Vision model. Runtime UI login opens the page, screenshots it, asks Vision for the captcha text, fills it through the planner-generated login steps, and verifies the post-login page before UI test planning continues.
- `auth_preflight_id` is an in-process reusable preflight token. Create run may reuse it only when the request fingerprint still matches and the preflight has not expired; otherwise create run re-runs auth preflight server-side.

### 4. Validation & Error Matrix

- New `test_type="auto"` on `/runs` or `/runs/preflight` -> `400` with allowed values `api, ui`.
- Auto auth without username/password or login body -> blocked `auth_preflight`, missing `username/password`.
- Manual auth without auth-like header/token -> blocked `auth_preflight`, missing `token_or_header`.
- Token/cookie acquired but protected read-only validation fails -> blocked create/preflight.
- API dynamic captcha returns image/context but no clear code while login requires captcha -> blocked, missing `captcha`.
- UI dynamic captcha without default Vision model -> blocked, missing `vision_model`.
- Valid `auth_preflight_id` with changed payload -> ignored; server re-runs preflight.

### 5. Good/Base/Bad Cases

- Good: API auto login gets a token, verifies 2-3 protected GET/HEAD/OPTIONS endpoints, then dispatches the worker with redacted auth headers and refresh config.
- Good: API dynamic captcha fetches `/captcha`, records context-field availability, and blocks before submitting `/login` when only an image is available.
- Good: UI dynamic captcha verifies Vision availability before launch and runtime login stops before UI cases when recognition or post-login verification fails.
- Base: legacy clients that send a token without `auth_mode` are treated like manual auth for compatibility.
- Bad: preflight marks manual Token/Header as ready without a protected read-only request.
- Bad: API preflight sends a captcha image to Vision/OCR or persists raw captcha/session/cookie values in response/logs.

### 6. Tests Required

- Preflight: `test_type="auto"` is rejected for new run entrypoints.
- Preflight: API dynamic captcha fetches context and does not submit login when captcha text is unavailable.
- Preflight: UI dynamic captcha blocks when no default Vision model exists.
- Preflight/create: token/cookie acquisition must be followed by protected read-only validation.
- Regression: serialized preflight and execution logs do not expose token, password, cookie, session, csrf/xsrf, or captcha values.
- Frontend build: `RunPage.vue` compiles with two modes, three auth modes, three captcha modes, `auth_credentials`, and `auth_preflight_id`.

### 7. Wrong vs Correct

#### Wrong

```python
headers, resolution = await resolve_auto_auth_headers(payload.auth_config, ...)
if resolution.ok:
    run_agent_task.delay(task.id, ..., auth_headers=headers)
```

#### Correct

```python
auth_preflight, headers, runtime_config, resolution = await _run_auth_preflight(...)
if not auth_preflight.can_start:
    raise HTTPException(status_code=400, detail="auth preflight required")
run_agent_task.delay(task.id, ..., auth_headers=headers, auth_config=runtime_config)
```

#### Wrong

```python
captcha_text = await llm_gateway.get_vision(db).ainvoke([captcha_image])
```

#### Correct

```python
captcha = await fetch_captcha_context(...)
if captcha_required and not captcha.captcha_text:
    block_preflight("static captcha required")
```

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
- Login inference must be credential-aware and schema-driven: prefer simple account-password endpoints such as `/login`, `/auth/login`, `/user/login`, or `/system/login`; prefer schemas/parameters with username/account/loginName/userName plus password/pwd fields; and penalize specialized markers such as `xcx`, `sms`, `email`, `wechat`, `oauth`, `sso`, `refresh`, `logout`, `register`, and `captcha` when their required fields cannot be supplied from `auth_config` or `auth_credentials`.
- OpenAPI endpoint descriptors used for auth inference must preserve path, summary, description, operationId, tags, request body schema, and parameters so scoring can use all available login intent signals.
- `token_path` supports simple dot paths such as `access_token`, `data.token`, and `$.data.token`. When omitted, common token fields may be inferred.
- Auto-login must inspect application envelopes before token extraction. HTTP 2xx with top-level `code`, `status`, or `status_code` outside success values (`0`, `200`, string equivalents such as `"0"`, `"200"`, `"ok"`, `"success"`) is a login failure, not a successful token-less login.
- Token extraction may infer common cased/nested token fields such as `data.access_token`, `data.token`, `data.Authorization`, top-level `authorization`, and token-like bare string `data`; it must never serialize the token in preflight responses, execution logs, or tool-call summaries.
- During API execution, if a non-`AUTH` request with an auth-like header returns HTTP `401/403` or JSON envelope `code/status/status_code` `401/403`, the runner may use `auth_config` to refresh once and retry that request once. Record an `api.auth_refresh` tool call with method/url/status metadata only.

### 4. Validation & Error Matrix

- Auth-required API + no token/header + no auto auth -> preflight `auth` check is `missing`, readiness is `blocked`, create run returns `400`.
- Auth-required API + auto auth login returns 4xx/5xx -> preflight is blocked and create run returns `400`.
- Auth-required API + auto auth login returns HTTP 200 with `{"code":500,"msg":"Password input error","data":null}` -> preflight is blocked as login/credential failure and must not suggest `token_path`.
- Auth-required API + OpenAPI lists `/xcxLogin`, `/smsLogin`, `/emailLogin`, and `/login` + username/password credentials -> inferred login URL is `/login`; preflight must not submit to a specialized endpoint requiring unsupplied fields such as `xcxCode`, `smsCode`, or `emailCode`.
- Auth-required API + auto auth succeeds but `token_path` is missing -> preflight is blocked and create run returns `400`.
- Auth-required API + auto auth succeeds -> preflight reports `auth_resolved=true`, create run injects `Authorization: Bearer <token>`.
- Non-auth API + auto auth fails -> warn only; do not block the run solely for optional auth failure.
- Manual token + refresh config + request returns envelope `{"code":401}` -> refresh auth, retry that request once, and keep token/password values out of persisted evidence.

### 5. Good/Base/Bad Cases

- Good: user provides `/auth/login`, login JSON body, and `data.token`; preflight proves the token can be acquired and the worker receives an Authorization header.
- Good: user provides username/password and the OpenAPI has both `/login` and specialized mini-program/SMS/email login variants; inference selects the simple password-login schema and maps credentials to fields such as `loginName`/`pwd`.
- Good: user provides a current token plus username/password/captcha/tenant; the runner refreshes after a 401/403 and retries one affected request.
- Base: user provides a direct Bearer token or API key header; preflight treats it as ready without attempting auto-login.
- Bad: all paths containing `login` are treated equally, causing `/xcxLogin` or `/smsLogin` to be attempted before `/login` when only username/password credentials were supplied.
- Bad: user only provides `X-Tenant` or setup notes; protected API run starts anyway and later reports skipped/unauthorized checks as if testing happened.

### 6. Tests Required

- Preflight: protected OpenAPI without credentials returns an `auth` check with `status="missing"`.
- Preflight: auto-login success returns `auth_resolved=true` without exposing the token in JSON.
- Regression: HTTP 200 application-level login failure is classified before token extraction and does not suggest `token_path`.
- Regression: true success-looking token-less login responses still ask for `token_path`.
- Regression: common nested/cased token fields such as `data.Authorization` are extracted without leaking the token.
- Regression: login inference with `/xcxLogin`, `/smsLogin`, `/emailLogin`, and `/login` chooses `/login` for username/password credentials.
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
  allow_out_of_schema_api_cases: bool = False
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
- Runtime execution budget:
  ```text
  API_MAX_EXECUTED_REQUESTS=120
  ```
- Additional runner result fields may include:
  ```python
  {
      "http_executed": int,
      "execution_budget": int | None,
      "budget_exhausted": bool,
      "environment_skipped": int,
      "budget_skipped": int,
      "candidate_total": int,
      "selected_total": int,
      "omitted": int,
      "request_selection": {
          "source": "api_cases|parsed_api_schema|all_safe_schema|safe_schema_fallback|schema_fallback|fallback_url",
          "candidate_total": int,
          "selected_total": int,
          "budget_limit": int | None,
          "budget_omitted": int,
          "runtime_budget_omitted": int,
          "omitted": int,
          "bounded": bool,
          "fallback_reason": str | None,
      },
      "advisory": int,
  }
  ```

### 3. Contracts

- `safe_read_only` is the default for unknown or real environments.
- `safe_read_only` and `safe_with_auth` must skip `POST`, `PUT`, `PATCH`, and `DELETE`; skipped requests are not failures.
- `write_allowed` may execute mutation requests only when explicitly selected by the caller.
- Auth-required endpoints must be detected from OpenAPI `security` metadata as well as explicit auth parameters.
- When auth is required but no token/header is provided, safe methods may run unauthorized checks, but positive business assertions must be skipped.
- If a Swagger document is served through a public proxy prefix, the loader may rewrite documented paths such as `/dev-api/*` to the reachable public prefix such as `/api/*`; the rewrite must be surfaced in preflight and run detail state.
- Status matching must consider both HTTP status and common JSON envelope status fields such as `code` or `status`.
- Status assertions must parse numeric strings, lists, and `one_of:401,403` style expectations exactly. Unknown formats must not silently become HTTP 200; `not_equals:200` may be supported as an explicit negative status expectation.
- API result entries may include `envelope_status_code`, `failure_type`, and `failure_reason` so reporter output can distinguish backend validation/contract failures from generic runner assertions.
- When `api_cases` are present, the API runner must build execution requests from those curated/generated cases before considering `parsed_api_schema`; do not fan out the full schema while ignoring the curated case set.
- Exception: objectives that explicitly ask for all GET/read-only coverage (for example "所有 GET", "全部 GET", "all GET requests", or "read-only endpoints") must not rely on LLM-generated samples. With a parsed OpenAPI schema, case generation and/or runner selection must deterministically derive safe `GET`/`HEAD`/`OPTIONS` smoke coverage from the schema, mark `request_selection.source="all_safe_schema"`, and cover up to `API_MAX_EXECUTED_REQUESTS`.
- If curated cases produce no executable request under `safe_read_only` or `safe_with_auth`, the runner may fall back to a bounded safe-method schema subset so read-only runs can still produce useful `GET`/`HEAD`/`OPTIONS` evidence.
- Evidence-evaluator API replan feedback must be bounded before it reaches `tc_generator`: list the documented method/path scope, remove out-of-schema path mentions, and forbid auth-bypass probes or mutation methods blocked by `api_execution_policy`.
- Model-generated API cases must be validated against `parsed_api_schema` before execution. Drop generated method/path pairs absent from the loaded OpenAPI schema unless `allow_out_of_schema_api_cases=true` was explicitly supplied. User-curated/suite cases may remain curated, but generated replans default to schema-only.
- Unsupported generated assertions are agent diagnostics, not product defects. Keep status assertions aligned with documented success responses for positive cases; keep JSON-path/schema/body assertions blocking only when grounded in the OpenAPI response schema, an explicit documented response example, or a user objective that directly names the product response field/value. Otherwise downgrade them to advisory/non-blocking and record `agent_case_diagnostics`.
- Generated root JSONPath equality such as `{"type": "json_path", "path": "$", "operator": "equals", "expected": "is_object"}` is a meta-assertion, not a product response value. Downgrade it to advisory when no response schema exists; when a matching response schema exists, rewrite it to an executable JSON type/schema assertion instead of comparing the whole response body to the literal string `is_object`.
- Mission-control objectives about agent, worker, DB session, planning, orchestration, ReAct traces, or delegation must not become blocking product API response assertions unless the target API schema or documented example exposes those fields. Track those concerns in mission/reporter diagnostics instead of API pass-rate failures.
- Objective-grounded product response assertions must not be downgraded just because a field name overlaps with runtime terms. Treat terms such as `session`, `planning`, or `agent` as mission-control only when runtime context is also present (for example worker, DB session, event loop, orchestration, ReAct trace, delegation, Celery, LangGraph). CamelCase fields directly named in the objective, such as `userName` from "user name", remain valid objective grounding.
- `API_MAX_EXECUTED_REQUESTS` bounds real outbound HTTP attempts for an API run. Policy/dependency/environment skips do not consume this budget. Bound request selection before execution and report omitted candidates through summary metadata (`candidate_total`, `selected_total`, `omitted`, `budget_skipped`, `request_selection`); do not append one `execution_budget_exhausted` result row for every omitted request.
- If a write method (`POST`, `PUT`, `PATCH`, `DELETE`) returns HTTP 405, treat it as `skip_type="environment_not_executable"` instead of a product failure. Record the 405 evidence, then skip later requests for the same origin + method without sending more traffic.
- AUTH negative probes that expect `401/403` still pass when HTTP status or JSON envelope status is unauthorized. If such a probe returns HTTP 2xx instead, record an advisory finding (`advisory=True`, `skip_type="auth_advisory"`) and exclude it from the main pass-rate failure count.
- AUTH negative probes that expect `401/403` must remove auth-like headers after merging default and case/template headers. Strip names such as `Authorization`, `Cookie`, `X-API-Key`, `API-Key`, and token/session/auth/csrf-like headers, even when generated case templates contain redacted placeholders.
- Positive and non-AUTH requests must keep runtime `auth_headers` when generated case templates contain redacted or stale auth-like placeholders; template headers may add non-conflicting real headers, including `X-API-Key`, but must not overwrite prepared credentials and must not send redacted placeholder values as credentials.
- Invalid-input `PARAM_VALIDATION` cases may pass when HTTP 2xx carries a clear business error envelope: top-level `code`/`status`/`status_code >= 400` or `success=false` plus a validation/error message. Business `code >= 500` should be stored as a warning/advisory field, not a main failure solely because the invalid input was rejected through the envelope. Auth failures remain strict.
- API response bodies persisted in `api_execution_result` or `execution_result` must be safe for JSON storage. Non-JSON and non-text content types are stored as a summary (`content_type`, byte count, preview note), and text values must strip NUL/control characters before persistence.

### 4. Validation & Error Matrix

- `safe_read_only` + mutation endpoint -> skip with `skipped=True` and a human-readable `skip_reason`.
- Auth-required endpoint + no credentials + positive assertion -> skip the positive assertion, do not report it as failed.
- Auth-required endpoint + no credentials + expected unauthorized response -> execute and pass when HTTP status or JSON envelope status matches 401/403.
- Swagger path prefix differs from reachable public prefix -> apply and record `api_path_prefix_rewrite`; do not send requests to the internal-only prefix.
- Evidence evaluator suggests `/non_existent_endpoint` after documented endpoints pass -> replan feedback replaces that path with an out-of-scope marker and `tc_generator` may only regenerate documented cases.
- Generated case targets `GET /missing` while the loaded OpenAPI has only `GET /get` and `GET /headers` -> drop the case, append `agent_case_diagnostics[].kind="out_of_scope_api_case"`, and do not execute an HTTP request.
- Generated JSON-path assertion is not present in the response schema, documented response example, or explicitly named product objective field/value -> set `blocking=false`, mark it advisory, and do not allow it to set `failed` or `bug_found`.
- Generated `json_path "$" equals "is_object"` against a schema-less 200 endpoint returning an object -> HTTP/status assertions may pass, the root meta assertion is advisory only, and final status remains `succeeded`/`PASS` when no real product failure exists.
- Generated objective-grounded assertion `$.session.status == "active"` for objective "Verify login session status is active" -> remains blocking even without a response schema; a mismatched response fails the API case.
- Generated objective-grounded assertion `$.userName == "Ada"` for objective "Verify user name is Ada" -> remains blocking; camelCase field tokenization must not downgrade it to advisory.
- JSON envelope returns `{"code": 401}` with HTTP 200 -> treat as unauthorized for matching and reporting.
- Assertion `expected: "one_of:401,403"` + actual HTTP 200 -> fail; actual HTTP 401 or 403 -> pass.
- Invalid-input negative case returns HTTP 200 with body `{"code": 500, "msg": "不能为空"}` -> pass the `PARAM_VALIDATION` rejection with `accepted_error_envelope=true` and warning metadata; do not create a blocking finding solely for the 5xx business code.
- Large OpenAPI schema with more executable requests than `API_MAX_EXECUTED_REQUESTS` -> select up to the budget, execute selected requests, keep omitted requests out of `results`, and report `budget_exhausted=true`, `budget_skipped=<omitted count>`, `omitted=<omitted count>`, and `request_selection`.
- Objective "测试所有 GET 请求" + OpenAPI has 107 safe endpoints + `API_MAX_EXECUTED_REQUESTS=120` -> derive schema-driven safe smoke requests for all 107 endpoints, use `request_selection.source="all_safe_schema"`, and do not replan merely because LLM `api_cases` would have been sparse.
- Objective "all GET requests" + OpenAPI has more safe endpoints than `API_MAX_EXECUTED_REQUESTS` -> execute a bounded deterministic subset, report `safe_endpoint_total`, `selected_safe_endpoint_total`, `omitted_safe_endpoint_total`, and `bounded=true`.
- Write request returns HTTP 405 from nginx or an upstream method gate -> mark that request environment-not-executable, then skip same-origin same-method write requests without counting them as failures.
- AUTH negative probe returns HTTP 200 with no unauthorized envelope -> record an advisory/security warning and keep main `failed` count unchanged.
- AUTH negative curated case has `request_template.headers.Authorization="[REDACTED]"` and target returns HTTP 200 with `{"code":401}` when no token is sent -> strip auth-like headers, execute without credentials, and pass the case.
- Non-AUTH curated case has `auth_headers.Authorization="Bearer real"`, `request_template.headers.Authorization="[REDACTED]"`, and `request_template.headers.X-API-Key="case-secret"` -> send `Bearer real`, skip the redacted placeholder, and retain non-conflicting real template headers such as `X-API-Key` and `X-Trace`.
- Binary export response such as `application/octet-stream` -> persist only a safe summary; no `\u0000` or raw control characters may appear in serialized execution logs.

### 5. Good/Base/Bad Cases

- Good: real-environment API run reports `executed=44`, `skipped=93`, `failed=0` when all executable read checks pass and write checks are intentionally skipped.
- Base: no credentials are provided; the report explains skipped auth-positive checks and recommends adding token/header or configuring login.
- Base: a real environment blocks writes at the gateway with 405; the report explains the environment limitation and does not turn hundreds of write probes into business failures.
- Base: an auth negative probe returns 200; the report keeps a warning/advisory finding without lowering the main pass rate.
- Base: a passing two-endpoint public API smoke may trigger an LLM replan for deeper assertions, but the second pass still executes only documented endpoints and records invalid generated requests as agent diagnostics.
- Base: a mission-control smoke may ask the agent to verify worker/session/planning stability; generated API response assertions for those internal concerns are advisory unless the target API contract directly documents matching response fields.
- Bad: skipped write requests or auth-positive checks are counted as failed, producing a false `BUG_FOUND` run.
- Bad: LLM replan text asks for `/non_existent_endpoint` or an auth-bypass probe, the generator creates it, the runner executes it, and the reporter marks the invented failure as a product bug.
- Bad: a run has 10 curated `api_cases` but the runner ignores them and fans out 850 schema-derived requests.
- Bad: budget exhaustion persists hundreds of `execution_budget_exhausted` result rows instead of focused result rows plus omitted metadata.
- Bad: an AUTH negative "no token" case inherits `Authorization` from `auth_headers` or a generated template and tests the authenticated path.
- Bad: a large OpenAPI document keeps sending every generated request until the Celery hard time limit kills the worker.
- Bad: binary export bytes or NUL/control characters are persisted directly into `Task.execution_log`, breaking JSONB casts or UI history parsing.

### 6. Tests Required

- Regression: source loader rewrites proxied Swagger paths and persists the rewrite contract.
- Regression: auth chain marks endpoints requiring OpenAPI `security` as auth-required.
- Regression: API runner skips mutation methods under safe policies and does not count skips as failures.
- Regression: reporter summaries include executed/skipped counts and keep skipped requests out of failed totals.
- Regression: reporter turns `backend_validation_contract` API result failures into backend validation contract findings.
- Regression: API runner prefers curated `api_cases` over `parsed_api_schema` when both are present.
- Regression: all-GET/read-only objectives deterministically select schema-derived safe endpoints up to the execution budget instead of relying on sparse LLM-generated `api_cases`.
- Regression: execution evaluator does not trigger repeated API replans when `request_selection.source="all_safe_schema"` has covered the schema-derived budget.
- Regression: evidence-evaluator API replan instructions remove out-of-schema path mentions and name only documented method/path scope.
- Regression: generated API cases targeting undocumented paths are filtered before execution; no HTTP request is sent for the hallucinated path.
- Regression: unsupported generated assertions are downgraded to advisory/non-blocking and appear in `agent_case_diagnostics`, not `bugs_found`.
- Regression: generated root JSONPath meta assertions such as `$ equals is_object` against schema-less endpoints are advisory/non-blocking, while grounded schema/example/objective JSONPath assertions still block and fail when the response value is actually wrong.
- Regression: objective-grounded product fields that overlap with runtime language (`session`, camelCase fields such as `userName`) remain blocking unless the objective clearly refers to agent/runtime control.
- Unit: LLM case/evaluator JSON parser handles fenced JSON, extra prose around JSON, near-JSON missing a comma between fields, and malformed partial output without crashing the node.
- Regression: safe policies fall back to bounded safe schema-derived requests when curated cases are all write-only or otherwise not executable.
- Regression: execution budget omits unselected or runtime-budget-exhausted requests from `results` and records `budget_exhausted=true`, `budget_skipped`, and `request_selection` metadata.
- Regression: `PARAM_VALIDATION` accepts HTTP 2xx business error envelopes with `code/status >=400` or `success=false` plus a message, including warning metadata for `code >=500`.
- Regression: same-origin same-method write requests are skipped after a 405 environment method block and do not lower pass rate.
- Regression: AUTH negative 200 responses are advisory/skipped while AUTH negative 401/403 responses still pass.
- Regression: curated AUTH negative cases strip template/default auth-like headers and pass on HTTP 200 plus envelope `code/status/status_code=401/403`.
- Regression: non-AUTH curated cases keep runtime auth headers even when templates contain redacted auth-like placeholders, and preserve non-conflicting real template headers such as `X-API-Key`.
- Regression: binary/control-character responses are persisted as safe summaries or sanitized text in both `api_execution_result` and `execution_result`.
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

#### Wrong

```python
for request in generated_requests:
    if http_executed_count >= max_executed_requests:
        results.extend(make_budget_skip(request) for request in generated_requests[index:])
        break
    response = await client.request(...)
    results.append({"passed": response.status_code < 400})
```

#### Correct

```python
selected, selection = select_requests_for_execution(candidates, max_executed_requests)
if http_executed_count >= max_executed_requests:
    selection["runtime_budget_omitted"] += len(selected[index:])
else:
    response = await client.request(...)
api_execution_result["request_selection"] = selection
```

#### Wrong

```python
state["agent_replan_feedback"] = model_decision["replan_instructions"]
state["api_cases"] = parsed_llm_cases
```

#### Correct

```python
state["agent_replan_feedback"] = sanitize_api_replan_instructions(model_decision, parsed_api_schema, policy)
api_cases, diagnostics = validate_generated_api_cases(parsed_llm_cases, parsed_api_schema, policy)
state["agent_case_diagnostics"] = diagnostics
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

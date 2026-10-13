# Deployment Guidelines

> Runtime topology and edge proxy contracts for TestClaw deployments.

---

## Scenario: Administrator Bootstrap and Explicit Password Recovery

### 1. Scope / Trigger

- Applies to local account initialization, the operator CLI, and deployment documentation.
- Environment changes must never silently overwrite an existing password.

### 2. Signatures

- `python -m app.cli.reset_admin_password --username NAME`
- `AuthService.reset_admin_password(db: AsyncSession, username: str, password: str) -> None`
- Configuration: `DATABASE_URL`, `DEFAULT_ADMIN_USERNAME`, `DEFAULT_ADMIN_PASSWORD`, `SECRET_KEY`.

### 3. Contracts

- Bootstrap creates only missing usernames. Existing hashes and account flags are preserved.
- The CLI requires trusted server/container and DB access, an explicit username, and two hidden terminal prompts; no password argument, environment input, or HTTP reset route.
- Reset updates only an existing active administrator's hash using the shared salted PBKDF2 helper. It never creates users/tables, promotes roles, or enables accounts.
- Accept 12–1024 characters, reject whitespace-only input, and preserve valid input exactly.
- Own/dispose the command's async engine in one event loop; avoid importing FastAPI startup.
- Never echo passwords, hashes, raw DB exceptions, or connection credentials.
- Existing JWTs are not revoked by password reset. Rotate `SECRET_KEY` and recreate all API/worker instances for global revocation; do not rotate `FERNET_KEY` for this purpose.

### 4. Validation & Error Matrix

- Successful committed reset: exit `0`; new password logs in and previous password fails.
- Missing/non-admin/inactive account or invalid password: exit `1`, no account modification.
- Mismatch, non-TTY, failed hidden input, cancellation, or DB error: nonzero exit with a safe message.
- Missing username or unsupported arguments: exit `2`, without echoing argument values.

### 5. Good/Base/Bad Cases

- Good: operator verifies deployment, runs the CLI, and checks old/new login behavior.
- Base: initial startup creates the configured admin once.
- Bad: overwriting hashes on every restart, using `--password`, or exposing unauthenticated recovery.

### 6. Tests Required

- Isolated SQLite tests cover salted hashes, bootstrap preservation, successful persistence, old/new login, and rejected accounts/input.
- CLI tests cover hidden-input failure, cancellation, no secret output, and uninitialized schema without bootstrap side effects.
- Never run reset tests against a deployment or developer database.

### 7. Wrong vs Correct

- Wrong: change `DEFAULT_ADMIN_PASSWORD` and restart to rotate an existing password.
- Correct: run the explicit CLI against the intended deployment's `DATABASE_URL`; subsequent startup preserves the reset hash.

## Scenario: Domain Behind Host Caddy, Optional Compose Nginx

### 1. Scope / Trigger

- Trigger: production domain traffic for `testclaw.oceancute.cn` is terminated by the host Caddy service, while app services run in Docker Compose.
- Code-spec depth is required because this is an infra integration contract involving ports, env keys, and service startup commands.
- Applies to `docker/docker-compose.yml`, `/etc/caddy/Caddyfile`, `frontend/Dockerfile`, and domain smoke checks.

### 2. Signatures

- Default app services:
  ```bash
  docker compose --env-file .env -f docker/docker-compose.yml up -d api worker frontend
  ```
- Optional Compose edge proxy:
  ```bash
  docker compose --env-file .env -f docker/docker-compose.yml --profile edge up -d nginx
  ```
- Caddy upstreams:
  ```text
  /api/*  -> 127.0.0.1:18000
  /health -> 127.0.0.1:18000
  /*      -> 127.0.0.1:15173
  ```

### 3. Contracts

- `api` publishes `127.0.0.1:18000:8000`.
- `frontend` publishes `127.0.0.1:15173:80`.
- The production frontend build uses `VITE_API_BASE_URL=/api/v1`.
- The frontend container serves `index.html` and SPA fallbacks with `Cache-Control: no-store`; hashed `/assets/` files may be cached immutably.
- `nginx` in Compose is optional and must stay behind the `edge` profile.
- `HTTP_PORT` only applies when starting the optional `edge` profile.
- Do not start Compose `nginx` on a host where Caddy owns port `80`.
- `api` and `worker` read the shared `../.env`; runtime API execution controls such as `API_MAX_EXECUTED_REQUESTS` must be configured there and both services recreated/restarted together.

### 4. Validation & Error Matrix

- Caddy owns port `80` and Compose starts only `api worker frontend` -> domain works through Caddy.
- Caddy owns port `80` and Compose starts `--profile edge nginx` -> Docker fails to bind `0.0.0.0:80`.
- Compose `nginx` is started without being on the Compose network -> nginx cannot resolve `api` or `frontend`.
- Frontend build misses `VITE_API_BASE_URL=/api/v1` -> browser calls the wrong API base path.
- Domain `/health` returns `200 {"status":"ok"}` -> Caddy-to-API routing is healthy.
- Domain `/` returns `Cache-Control: no-store` so browser sessions do not keep an old frontend shell after deploy.

### 5. Good/Base/Bad Cases

- Good: deploy app containers with `api worker frontend`, let host Caddy serve the public domain, then smoke test `/health`, `/`, and `/api/v1/runs/preflight`.
- Base: local docker-only deployment starts `--profile edge nginx` on an unused `HTTP_PORT`.
- Bad: default Compose startup launches nginx and competes with Caddy for port `80`.

### 6. Tests Required

- Config: `docker compose --env-file .env -f docker/docker-compose.yml config --quiet`.
- Runtime: `docker compose --env-file .env -f docker/docker-compose.yml ps` shows `api`, `worker`, and `frontend` running.
- Domain: `curl -i https://testclaw.oceancute.cn/health` returns `200`.
- Frontend: the loaded asset contains `Testing Agent Workspace`.
- API smoke: authenticated `POST /api/v1/runs/preflight` returns `readiness: "ready"` for a reachable Swagger URL when models and environments are configured.

### 7. Wrong vs Correct

#### Wrong

```bash
docker compose --env-file .env -f docker/docker-compose.yml up -d api worker frontend nginx
```

#### Correct

```bash
docker compose --env-file .env -f docker/docker-compose.yml up -d api worker frontend
```

#### Correct for docker-only edge hosting

```bash
docker compose --env-file .env -f docker/docker-compose.yml --profile edge up -d nginx
```

## Scenario: Frontend API Base URL Contract

### 1. Scope / Trigger

- Trigger: the browser may run against a local Vite server while the API is served from a different origin, or from production where `/api/v1` is same-origin.
- Applies to `frontend/src/lib/api.ts` and any browser API URL that bypasses axios, including `EventSource`, asset URLs, and direct download links.

### 2. Signatures

- Environment key:
  ```text
  VITE_API_BASE_URL=http://127.0.0.1:8000/api/v1
  ```
- Frontend helper:
  ```typescript
  apiUrl(path: string, params?: Record<string, string | number | boolean | null | undefined>): string
  ```

### 3. Contracts

- Axios `baseURL` and direct browser API URLs must use the same exported API base helper.
- Default deployments preserve same-origin relative URLs by falling back to `/api/v1`.
- Local split-origin development may set `VITE_API_BASE_URL` to an absolute backend URL.
- Remote screenshot URLs from storage payloads stay unchanged; only backend fallback routes use the API URL helper.

### 4. Validation & Error Matrix

- `VITE_API_BASE_URL` unset + SSE stream -> browser requests `/api/v1/runs/{id}/stream` on the current origin.
- `VITE_API_BASE_URL=http://127.0.0.1:8000/api/v1` + SSE stream -> browser requests `http://127.0.0.1:8000/api/v1/runs/{id}/stream`.
- Hard-coded `/api/v1` in direct `EventSource` or asset URLs during split-origin local dev -> Vite proxy may target Docker-only upstreams and return `500`.

### 5. Good/Base/Bad Cases

- Good: ``new EventSource(apiUrl(`/runs/${runId}/stream`, { token }))``.
- Base: ``api.get(`/runs/${runId}`)`` continues to rely on the shared axios client.
- Bad: ``new EventSource(`/api/v1/runs/${runId}/stream?token=${token}`)``.

### 6. Tests Required

- Frontend type-check: `npm exec tsc -- --noEmit`.
- Frontend build: `npm run build`.
- Smoke: with `VITE_API_BASE_URL=http://127.0.0.1:8000/api/v1`, run detail SSE and screenshot fallback URLs hit the backend origin, not the Vite proxy.

### 7. Wrong vs Correct

#### Wrong

```typescript
const url = `/api/v1/runs/${runId}/stream`
new EventSource(token ? `${url}?token=${token}` : url)
```

#### Correct

```typescript
new EventSource(apiUrl(`/runs/${runId}/stream`, { token }))
```

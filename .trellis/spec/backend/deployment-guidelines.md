# Deployment Guidelines

> Runtime topology and edge proxy contracts for TestClaw deployments.

---

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
- `nginx` in Compose is optional and must stay behind the `edge` profile.
- `HTTP_PORT` only applies when starting the optional `edge` profile.
- Do not start Compose `nginx` on a host where Caddy owns port `80`.

### 4. Validation & Error Matrix

- Caddy owns port `80` and Compose starts only `api worker frontend` -> domain works through Caddy.
- Caddy owns port `80` and Compose starts `--profile edge nginx` -> Docker fails to bind `0.0.0.0:80`.
- Compose `nginx` is started without being on the Compose network -> nginx cannot resolve `api` or `frontend`.
- Frontend build misses `VITE_API_BASE_URL=/api/v1` -> browser calls the wrong API base path.
- Domain `/health` returns `200 {"status":"ok"}` -> Caddy-to-API routing is healthy.

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

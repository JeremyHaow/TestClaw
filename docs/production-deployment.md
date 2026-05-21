# Production Deployment

This repo ships a Docker Compose production path for `testclaw.oceancute.cn`:

- `nginx` listens on host port `80`, matches `testclaw.oceancute.cn`, serves the Vue app, and reverse-proxies `/api/` to FastAPI.
- `frontend` builds with `npm run build` and serves static assets with nginx on the internal Docker network.
- `api` runs FastAPI on internal port `8000`.
- `worker` runs the Celery worker against the same Redis and database settings.
- `db` uses PostgreSQL with pgvector.
- `redis` is the Celery broker/result backend.

## Required Server Setup

1. Point DNS `testclaw.oceancute.cn` at the server.
2. Install Docker Engine and Docker Compose v2.
3. Open inbound TCP `80` to the server, or put another TLS/load-balancer layer in front of this compose stack.
4. Clone the repo on the server.

## Environment

Create `.env` from the example:

```bash
cp .env.example .env
```

Set these keys before starting production:

- `POSTGRES_DB`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `DATABASE_URL` matching the same database credentials, for example `postgresql+asyncpg://postgres:<password>@db:5432/testclaw`
- `REDIS_URL=redis://redis:6379/0`
- `FERNET_KEY`, generated with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
- `SECRET_KEY`, a long random secret
- `DEFAULT_ADMIN_USERNAME`
- `DEFAULT_ADMIN_PASSWORD`
- `BACKEND_CORS_ORIGINS=http://testclaw.oceancute.cn,https://testclaw.oceancute.cn`
- `DEFAULT_OPENAI_API_KEY` and model/base URL settings if agent execution will call an OpenAI-compatible provider
- `VITE_API_BASE_URL=/api/v1`
- `HTTP_PORT=80`

Do not commit `.env`.

## Commands

Validate compose config:

```bash
docker compose --env-file .env -f docker/docker-compose.yml config
```

Build images:

```bash
docker compose --env-file .env -f docker/docker-compose.yml build api worker frontend
```

Start the stack:

```bash
docker compose --env-file .env -f docker/docker-compose.yml up -d
```

Check status and logs:

```bash
docker compose --env-file .env -f docker/docker-compose.yml ps
docker compose --env-file .env -f docker/docker-compose.yml logs -f nginx api worker
```

Smoke checks from the server:

```bash
curl -i http://127.0.0.1/health
curl -I -H 'Host: testclaw.oceancute.cn' http://127.0.0.1/
```

The app should then be reachable at `http://testclaw.oceancute.cn/`. Configure HTTPS at the outer proxy/load-balancer layer, or extend the nginx service with certificates if this server terminates TLS directly.

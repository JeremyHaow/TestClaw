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

## Administrator password management

### Bootstrap configuration versus database credentials

`DEFAULT_ADMIN_USERNAME` and `DEFAULT_ADMIN_PASSWORD` are **creation-time defaults**.
On startup, TestClaw creates an active administrator only when the configured
username does not exist. For an existing username, its stored password hash and
account flags are left unchanged, including after an application restart.

Changing `DEFAULT_ADMIN_PASSWORD` in `.env` or a container environment **does not
rotate or recover an existing account's password**. Changing
`DEFAULT_ADMIN_USERNAME` does not rename the old account: if the new username is
absent, startup creates another administrator. Set strong, unique bootstrap
credentials and a random `SECRET_KEY` before the first production startup; the
defaults in `app/config.py` are intended for development. Keep bootstrap secrets
protected even after creation, since a new database would use them again.

### Explicit password rotation or recovery

The reset tool is for trusted server operators with shell/container execution
access and permission to write the application's database. Restrict SSH, Docker
access, deployment files, and database credentials to those operators; possession
of an application login alone does not grant this command's server access. There
is no public HTTP reset endpoint and the forgotten password is not required.

Use the deployed version containing this tool, an interactive terminal, and the
same `DATABASE_URL` as the API. Confirm the intended deployment/database and exact
existing username before running the command. The examples below use `admin`;
replace it with the actual account name.

From the repository root in a local/server Python environment:

```bash
uv run python -m app.cli.reset_admin_password --username admin
```

With the virtual environment already activated, `python -m
app.cli.reset_admin_password --username admin` is equivalent. Configuration is
loaded from the process environment and the `.env` in the current directory,
just as for the API; a relative SQLite path is also relative to that directory.

For a running Compose deployment, from the repository root:

```bash
docker compose --env-file .env -f docker/docker-compose.yml exec api \
  python -m app.cli.reset_admin_password --username admin
```

`exec` uses the running API container's environment, not edits to the host `.env`
made after that container was created. If the API is stopped but the database is
running, use the same deployed image and reviewed `.env` with a one-off container:

```bash
docker compose --env-file .env -f docker/docker-compose.yml run --rm --no-deps api \
  python -m app.cli.reset_admin_password --username admin
```

Keep the terminal enabled (do not use `-T`, pipes, or redirected stdin). Enter a
new, unique password twice at the hidden prompts. It must contain 12–1024
characters and cannot consist only of whitespace; valid input is stored exactly
as entered. Do not put passwords in command arguments, environment variables,
shell history, or scripts. The command supports no password argument or password
environment variable, and refuses to fall back to echoed input.

The tool updates only the selected active administrator's salted PBKDF2-SHA256
password hash. It never creates accounts or tables, promotes a user, enables an
inactive account, or starts the API. Missing/non-admin/inactive accounts, invalid
passwords, mismatches, cancelled input, or database errors produce a nonzero exit
status. Resolve a missing account or schema by checking the deployment and its
normal initialization/migration procedure, not by deleting the database.

After the command reports success (exit status `0`), log in with the new password
and confirm that the previous password is rejected. No API restart is needed;
subsequent startups preserve the reset password. Keep the new password in your
approved password manager. Updating bootstrap configuration alone is never a
substitute for this command.

### Existing sessions and incident response

Password reset prevents future logins with the old password, but **does not revoke
already-issued JWT access tokens**. They remain valid until their expiry
(`ACCESS_TOKEN_EXPIRE_MINUTES`, default 60 minutes). If immediate revocation is
required, rotate `SECRET_KEY` to a new random secret and recreate all API/worker
instances that use it. This invalidates every user's existing token and requires
everyone to log in again. In Compose, after securely updating the key in `.env`:

```bash
docker compose --env-file .env -f docker/docker-compose.yml up -d --force-recreate api worker
```

Do not rotate `FERNET_KEY` for this purpose; it protects stored encrypted provider
secrets and is unrelated to password hashes or JWT signing.

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

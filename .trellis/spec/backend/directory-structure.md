# Directory Structure

> How backend code is organized in this project.

---

## Directory Layout

```
app/
├── main.py              # FastAPI app factory, lifespan, CORS, router mount
├── config.py            # Pydantic Settings (env-based), lru_cache singleton
├── database.py          # SQLAlchemy async engine, sessionmaker, Base, get_db
├── api/
│   ├── router.py        # Top-level APIRouter, includes all v1 routers
│   └── v1/              # Feature routers (one file per feature)
├── agent/
│   ├── graph.py         # LangGraph StateGraph build + compile
│   ├── state.py         # AgentState TypedDict definition
│   ├── prompts.py       # All LLM prompt templates
│   └── nodes/           # One file per agent node, each exports async run()
├── core/
│   ├── dependencies.py  # FastAPI Depends: DbSession, CurrentUser, pagination
│   ├── llm_gateway.py   # LLM client factory (LangChain)
│   └── security.py      # JWT encode/decode, password hashing
├── models/              # SQLAlchemy ORM models, one file per model
├── schemas/             # Pydantic schemas, one file per feature
├── services/            # Service classes (CRUD), one file per feature, module-level singleton
├── tools/               # LangChain @tool wrappers and async utility functions
└── worker/              # Celery app + task definitions
```

## Conventions

- **One router per feature file** in `app/api/v1/`, mounted in `app/api/router.py`
- **One model per file** in `app/models/`, inheriting from `app.database.Base`
- **One schema per file** in `app/schemas/`, using `ORMModel` base for ORM-compatible schemas
- **One service class per file** in `app/services/`, instantiated as module-level singleton
- **One node per file** in `app/agent/nodes/`, each exporting `async def run(state) -> AgentState`
- **Tools** in `app/tools/` are LangChain `@tool` decorated or async functions

## Naming Conventions

- Files: `snake_case.py`
- Classes: `PascalCase`
- Functions/variables: `snake_case`
- Constants: `UPPER_SNAKE_CASE`
- Router prefix: `/kebab-case` (e.g., `/api-tests`, `/test-cases`)
- Table names: `snake_case` plural (e.g., `tasks`, `test_runs`)

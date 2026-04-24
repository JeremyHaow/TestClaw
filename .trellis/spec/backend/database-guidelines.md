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

## Common Mistakes

- Don't forget `await` on all DB operations
- Don't use `session.expire_on_commit=True` (default) — use `expire_on_commit=False`
- Don't mix sync and async SQLAlchemy patterns

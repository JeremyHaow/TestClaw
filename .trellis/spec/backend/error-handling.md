# Error Handling

> How errors are handled in this project.

---

## API Layer (FastAPI Routes)

```python
# Use HTTPException for client errors
from fastapi import HTTPException

# 404 Not Found
task = await task_service.get(db, task_id)
if task is None:
    raise HTTPException(status_code=404, detail="Task not found")

# 400 Bad Request
if current_status not in ("queued", "running"):
    raise HTTPException(status_code=400, detail="Task is not in a cancellable state")

# 401 Unauthorized (via dependency)
raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication credentials")
```

## Agent Nodes (LangGraph)

```python
# Agent nodes use try/except with logging.warning — never raise
try:
    llm = await llm_gateway.get_planner(db)
    resp = await llm.ainvoke([HumanMessage(content=prompt)])
    # parse response...
except Exception as e:
    logger.warning("NodeName LLM call failed: %s, using fallback", e)
    # Use fallback/default value
```

## Service Layer

```python
# Services let exceptions propagate — caller handles
async def create(self, db: AsyncSession, **kwargs) -> Task:
    task = Task(**kwargs)
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task
```

## Celery Workers

```python
# Wrap in try/except, log errors, update task status
try:
    final_state = await agent_graph.ainvoke({...})
except Exception as e:
    logger.error("Agent task failed: %s", e)
    task.status = TaskStatus.FAILED
    await db.commit()
```

## Error Response Format

All API errors follow FastAPI's default:
```json
{"detail": "Human-readable error message"}
```

## Rules

- **API routes**: Raise `HTTPException` with appropriate status code
- **Agent nodes**: Never raise — catch all, log warning, use fallback
- **Services**: Let exceptions propagate to caller
- **Tools**: Return error dicts, never raise to caller
- **Never expose internal errors** to API clients — use generic messages
- **Always log** the full exception before returning a fallback

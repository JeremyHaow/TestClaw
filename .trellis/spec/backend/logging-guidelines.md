# Logging Guidelines

> How logging is done in this project.

---

## Setup

```python
import logging
logger = logging.getLogger(__name__)
```

Standard Python `logging` module. No structured logging library. Each module creates its own logger.

## Log Levels

| Level | When to Use |
|-------|-------------|
| `logger.debug()` | Verbose internal state, rarely used |
| `logger.info()` | Startup events, major state changes |
| `logger.warning()` | Recoverable errors, fallbacks triggered, LLM call failures |
| `logger.error()` | Unrecoverable failures, Celery task failures |

## Patterns

```python
# Warning with exception context (most common in agent nodes)
logger.warning("Planner LLM call failed: %s, using fallback", e)

# Warning with detail
logger.warning("Failed to persist test cases: %s", e)

# Info for state transitions
logger.info("Input classified as: %s (source=%s)", input_type, source[:80])
```

## What to Log

- LLM call failures (always as `warning`)
- Fallback usage (always as `warning`)
- Celery dispatch failures
- Database persistence failures
- Input classification results

## What NOT to Log

- API keys, tokens, passwords
- Full request/response bodies (log summaries only)
- PII (user emails, names)
- Sensitive configuration values

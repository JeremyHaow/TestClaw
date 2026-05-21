"""
Token Budget Manager — Prevents LLM context overflow for large API specs.

Inspired by Anything Analyzer's DataAssembler token budgeting. Applies a
configurable token budget by iteratively truncating the largest items,
ensuring the prompt fits within LLM context limits.
"""

import json
from dataclasses import dataclass


@dataclass
class BudgetResult:
    """Result of applying a token budget."""
    items: list[dict]
    total_chars: int
    truncated_count: int
    within_budget: bool


# Approximate chars per token (conservative estimate for mixed CJK/ASCII)
CHARS_PER_TOKEN = 3

# Default budget: 30K tokens ≈ 90K chars
DEFAULT_TOKEN_BUDGET = 30000
DEFAULT_CHAR_BUDGET = DEFAULT_TOKEN_BUDGET * CHARS_PER_TOKEN

# Minimum chars per item after truncation
MIN_ITEM_CHARS = 200

# Maximum truncation rounds
MAX_ROUNDS = 20


def estimate_tokens(text: str) -> int:
    """Estimate token count from text. Conservative for mixed content."""
    return len(text) // CHARS_PER_TOKEN


def truncate_value(value: str, max_chars: int) -> str:
    """Truncate a string value, preserving structure."""
    if len(value) <= max_chars:
        return value
    if max_chars < 50:
        return value[:max_chars] + "..."
    half = (max_chars - 5) // 2
    return value[:half] + "\n...\n" + value[-half:]


def apply_token_budget(
    items: list[dict],
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    size_key: str = "body",
    min_chars: int = MIN_ITEM_CHARS,
) -> BudgetResult:
    """Apply token budget to a list of items by truncating largest items.

    Iteratively finds the item with the largest `size_key` field and
    truncates it to 1/4 of its current size, until total is within budget
    or all items are at minimum size.

    Args:
        items: List of dicts (each may have a `size_key` field with string content).
        token_budget: Maximum tokens allowed.
        size_key: The field name containing the content to truncate.
        min_chars: Minimum size for any item after truncation.

    Returns:
        BudgetResult with possibly modified items.
    """
    char_budget = token_budget * CHARS_PER_TOKEN

    # Calculate current total
    def item_chars(item: dict) -> int:
        val = item.get(size_key, "")
        return len(str(val))

    total = sum(item_chars(item) for item in items)

    if total <= char_budget:
        return BudgetResult(
            items=items,
            total_chars=total,
            truncated_count=0,
            within_budget=True,
        )

    # Work on a copy
    items = [dict(item) for item in items]
    truncated = 0

    for _ in range(MAX_ROUNDS):
        if total <= char_budget:
            break

        # Find the largest item
        sizes = [(i, item_chars(item)) for i, item in enumerate(items)]
        sizes.sort(key=lambda x: x[1], reverse=True)

        if not sizes or sizes[0][1] <= min_chars:
            break  # Can't truncate further

        idx, size = sizes[0]
        new_size = max(size // 4, min_chars)
        if new_size >= size:
            break

        val = str(items[idx].get(size_key, ""))
        items[idx][size_key] = truncate_value(val, new_size)
        total = total - size + new_size
        truncated += 1

    return BudgetResult(
        items=items,
        total_chars=total,
        truncated_count=truncated,
        within_budget=total <= char_budget,
    )


def apply_schema_budget(
    endpoints: list[dict],
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> list[dict]:
    """Apply token budget to parsed API schema endpoints.

    Truncates large request_body_schema and response_schema fields
    while preserving endpoint metadata (path, method, summary).

    Args:
        endpoints: Parsed API endpoint list.
        token_budget: Maximum tokens for the schema summary.

    Returns:
        Possibly truncated endpoint list.
    """
    if not endpoints:
        return endpoints

    # Serialize each endpoint to estimate its size
    items = []
    for ep in endpoints:
        ep_str = json.dumps(ep, ensure_ascii=False, default=str)
        items.append({
            "endpoint": ep,
            "body": ep_str,
        })

    result = apply_token_budget(items, token_budget, size_key="body")

    # Reconstruct endpoints from possibly truncated bodies
    output = []
    for item in result.items:
        ep = item["endpoint"]
        # If the body was truncated, the endpoint dict is still intact
        # but we should truncate large nested fields
        body = ep.get("request_body_schema")
        if body and isinstance(body, dict):
            body_str = json.dumps(body, ensure_ascii=False)
            if len(body_str) > 1000:
                ep["request_body_schema"] = {
                    "_truncated": True,
                    "_original_size": len(body_str),
                    **{k: v for k, v in list(body.items())[:5]},
                }
        resp = ep.get("response_schema")
        if resp and isinstance(resp, dict):
            resp_str = json.dumps(resp, ensure_ascii=False)
            if len(resp_str) > 1000:
                ep["response_schema"] = {
                    "_truncated": True,
                    "_original_size": len(resp_str),
                    **{k: v for k, v in list(resp.items())[:5]},
                }
        output.append(ep)

    return output


def budget_summary_text(text: str, max_tokens: int = DEFAULT_TOKEN_BUDGET) -> str:
    """Truncate a text block to fit within a token budget.

    Preserves the beginning and end, truncates the middle.
    """
    max_chars = max_tokens * CHARS_PER_TOKEN
    if len(text) <= max_chars:
        return text
    return truncate_value(text, max_chars)

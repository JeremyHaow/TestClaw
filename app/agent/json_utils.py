from __future__ import annotations

import json
import re
from typing import Any, Literal


JsonKind = Literal["object", "array", "any"]


_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.S)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")
_MISSING_COMMA_BEFORE_KEY_RE = re.compile(
    r'([}\]"0-9]|true|false|null)\s+(?="[^"]+"\s*:)',
    re.I,
)


def _strip_markdown_fence(text: str) -> str:
    match = _FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1].rsplit("```", 1)[0]
    return stripped.strip()


def _balanced_json_fragment(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    if stripped[0] in "{[":
        direct = _scan_balanced_from(stripped, 0)
        if direct:
            return direct

    for index, char in enumerate(stripped):
        if char in "{[":
            fragment = _scan_balanced_from(stripped, index)
            if fragment:
                return fragment
    return stripped


def _scan_balanced_from(text: str, start: int) -> str:
    stack: list[str] = []
    in_string = False
    escape = False
    pairs = {"}": "{", "]": "["}

    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]":
            if not stack or stack[-1] != pairs[char]:
                return ""
            stack.pop()
            if not stack:
                return text[start:index + 1].strip()
    return ""


def _repair_near_json(text: str) -> str:
    repaired = _TRAILING_COMMA_RE.sub(r"\1", text.strip())
    repaired = _MISSING_COMMA_BEFORE_KEY_RE.sub(r"\1, ", repaired)
    return repaired


def _kind_matches(parsed: Any, expected: JsonKind) -> bool:
    if expected == "any":
        return isinstance(parsed, (dict, list))
    if expected == "object":
        return isinstance(parsed, dict)
    return isinstance(parsed, list)


def parse_llm_json(content: str, *, expected: JsonKind = "any") -> Any | None:
    """Extract and parse the first JSON object/array from an LLM response."""
    raw = str(content or "").strip()
    if not raw:
        return None

    candidates = []
    fenced = _strip_markdown_fence(raw)
    balanced = _balanced_json_fragment(fenced)
    for candidate in (fenced, balanced, raw):
        candidate = candidate.strip()
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    for candidate in candidates:
        for attempt in (candidate, _repair_near_json(candidate)):
            try:
                parsed = json.loads(attempt)
            except Exception:
                continue
            if _kind_matches(parsed, expected):
                return parsed
    return None


def parse_llm_json_object(content: str) -> dict[str, Any]:
    parsed = parse_llm_json(content, expected="object")
    return parsed if isinstance(parsed, dict) else {}


def parse_llm_json_array(content: str) -> list[Any]:
    parsed = parse_llm_json(content, expected="array")
    return parsed if isinstance(parsed, list) else []

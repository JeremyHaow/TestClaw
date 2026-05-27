from __future__ import annotations

import shlex
from typing import Any

from app.core.redaction import redact_sensitive_data

PLAYWRIGHT_UI_ACTION_TYPES = {
    "open",
    "goto",
    "click_ref",
    "fill_ref",
    "snapshot",
    "screenshot",
    "assert_visible",
    "wait_for",
}

_HIGH_RISK_ACTION_TYPES = {"run_code", "run-code", "eval"}


def _text(value: Any, *, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _quote(value: Any) -> str:
    return shlex.quote(str(value))


def _normalize_type(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def _ref(action: dict[str, Any]) -> str:
    return _text(action.get("ref") or action.get("target_ref") or action.get("target"), limit=80)


def _structured_source(action_type: str, action: dict[str, Any]) -> str:
    label = _text(action.get("label") or action.get("reason") or "", limit=120)
    return f"structured {action_type}" + (f": {label}" if label else "")


def _base_spec(
    *,
    action: dict[str, Any],
    action_type: str,
    command: str,
    kind: str = "command",
    normalization: str | None = None,
    expected: str | None = None,
    skip: bool = False,
    risk: str = "safe_ui_action",
) -> dict[str, Any]:
    payload = {
        "command": command,
        "source_command": _structured_source(action_type, action),
        "kind": kind,
        "skill": "playwright-cli",
        "transport": "playwright-cli",
        "agent_action": redact_sensitive_data(action),
        "agent_action_type": action_type,
        "risk": risk,
    }
    if normalization:
        payload["normalization"] = normalization
    if expected is not None:
        payload["expected"] = expected
    if skip:
        payload["skip"] = True
    return payload


def _blocked_spec(action: dict[str, Any], action_type: str, reason: str, *, risk: str) -> dict[str, Any]:
    spec = _base_spec(
        action=action,
        action_type=action_type,
        command="",
        kind="unsupported",
        normalization=reason,
        skip=True,
        risk=risk,
    )
    if risk in {"high_risk", "invalid"}:
        spec["blocked"] = True
    return spec


def compile_playwright_ui_action(
    action: dict[str, Any],
    *,
    target_url: str = "",
) -> dict[str, Any]:
    """Compile one structured UI action into the playwright-cli spec used by ui_runner."""
    action_type = _normalize_type(action.get("type") or action.get("action_type") or action.get("name"))
    if not action_type:
        return _blocked_spec(action, "unknown", "Structured UI action is missing type.", risk="invalid")

    if action_type in _HIGH_RISK_ACTION_TYPES:
        return _blocked_spec(
            action,
            action_type,
            "Blocked arbitrary code execution in structured Playwright action; use bounded actions instead.",
            risk="high_risk",
        )

    if action_type not in PLAYWRIGHT_UI_ACTION_TYPES:
        return _blocked_spec(
            action,
            action_type,
            f"Unsupported structured Playwright action '{action_type}'.",
            risk="unsupported",
        )

    if action_type in {"open", "goto"}:
        url = _text(action.get("url") or action.get("target_url") or target_url, limit=500)
        command = action_type if not url and action_type == "open" else f"{action_type} {url}".strip()
        return _base_spec(
            action=action,
            action_type=action_type,
            command=command,
            normalization="Compiled structured navigation action to playwright-cli command.",
        )

    if action_type == "click_ref":
        ref = _ref(action)
        if not ref:
            return _blocked_spec(action, action_type, "click_ref requires ref.", risk="invalid")
        return _base_spec(
            action=action,
            action_type=action_type,
            command=f"click {ref}",
            normalization="Compiled structured click_ref action to playwright-cli click.",
        )

    if action_type == "fill_ref":
        ref = _ref(action)
        if not ref:
            return _blocked_spec(action, action_type, "fill_ref requires ref.", risk="invalid")
        value = action.get("value")
        if value is None:
            return _blocked_spec(action, action_type, "fill_ref requires value.", risk="invalid")
        submit = " --submit" if action.get("submit") else ""
        return _base_spec(
            action=action,
            action_type=action_type,
            command=f"fill {ref} {_quote(value)}{submit}",
            normalization="Compiled structured fill_ref action to playwright-cli fill.",
        )

    if action_type == "snapshot":
        return _base_spec(
            action=action,
            action_type=action_type,
            command="snapshot",
            normalization="Compiled structured snapshot action to playwright-cli snapshot.",
        )

    if action_type == "screenshot":
        return _base_spec(
            action=action,
            action_type=action_type,
            command="screenshot",
            kind="screenshot",
            normalization="Compiled structured screenshot action to run-scoped screenshot evidence.",
        )

    if action_type == "assert_visible":
        expected = _text(action.get("text") or action.get("expected") or action.get("target"), limit=300)
        return _base_spec(
            action=action,
            action_type=action_type,
            command="snapshot",
            kind="assert_snapshot_contains",
            expected=expected or None,
            normalization="Compiled structured assert_visible action to snapshot visibility assertion.",
        )

    expected = _text(action.get("text") or action.get("expected") or "", limit=300)
    return _base_spec(
        action=action,
        action_type=action_type,
        command="snapshot",
        kind="assert_snapshot_contains" if expected else "smart_wait",
        expected=expected or None,
        normalization="Compiled structured wait_for action to bounded snapshot observation.",
    )


def compile_playwright_ui_actions(
    actions: Any,
    *,
    target_url: str = "",
) -> list[dict[str, Any]]:
    if not isinstance(actions, list):
        return []
    specs: list[dict[str, Any]] = []
    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            specs.append(
                _blocked_spec(
                    {"index": index, "value": action},
                    "invalid",
                    "Structured UI action must be an object.",
                    risk="invalid",
                )
            )
            continue
        specs.append(compile_playwright_ui_action(action, target_url=target_url))
    return specs

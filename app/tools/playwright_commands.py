import json
import re


SUPPORTED_PLAYWRIGHT_COMMANDS = {
    "open",
    "snapshot",
    "click",
    "fill",
    "type",
    "screenshot",
    "go-back",
    "reload",
    "goto",
    "select",
    "hover",
    "press",
    "upload",
    "check",
    "uncheck",
    "eval",
    "run-code",
    "resize",
    "state-save",
    "state-load",
    "cookie-set",
    "cookie-clear",
    "dialog-accept",
    "dialog-dismiss",
}

_WAIT_COMMANDS = {"wait", "sleep", "pause", "timeout"}
_ASSERT_COMMANDS = {"assert", "expect"}
_ASSERT_VISIBLE_COMMANDS = {
    "assert_visible",
    "assert-visible",
    "assertvisible",
    "ui.assert_visible",
}
_VISIBLE_SELECTOR_SNAPSHOT_TERMS = {
    "a": "link",
    "button": "button",
    "h1": "heading",
    "h2": "heading",
    "h3": "heading",
    "h4": "heading",
    "h5": "heading",
    "h6": "heading",
    "input": "textbox",
    "select": "combobox",
    "textarea": "textbox",
}
_SNAPSHOT_REF_TOKEN = re.compile(r"(?P<quote>['\"]?)\[ref=(?P<ref>[A-Za-z0-9_-]+)\](?P=quote)")
_RUN_CODE_REF_LOCATOR = re.compile(
    r"page\.locator\(\s*['\"]\[ref=[A-Za-z0-9_-]+\]['\"]\s*\)",
    re.I,
)
_VIEWPORT_ALIASES = {
    "set_viewport_size",
    "set-viewport-size",
    "setviewportsize",
    "set_viewport",
    "set-viewport",
    "viewport",
    "viewport-size",
}
_RESIZE_RE = re.compile(r"(?P<w>\d{2,5})\s*(?:x|,|\s)\s*(?P<h>\d{2,5})", re.I)


def strip_playwright_cli_prefix(command: str) -> str:
    stripped = command.strip()
    if stripped.startswith("playwright-cli "):
        return stripped[len("playwright-cli "):].strip()
    return stripped


def command_name(command: str) -> str:
    stripped = strip_playwright_cli_prefix(command)
    return stripped.split(maxsplit=1)[0].lower() if stripped.split() else ""


def _strip_wrapping_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _extract_snapshot_assertion(command: str) -> str | None:
    match = re.search(r"\bcontains\b\s+(.+)$", command, flags=re.IGNORECASE)
    if not match:
        return None
    return _strip_wrapping_quotes(match.group(1))


def _extract_visible_assertion(command: str) -> str | None:
    parts = command.split(maxsplit=1)
    if len(parts) < 2:
        return None
    target = _strip_wrapping_quotes(parts[1]).strip()
    if target.lower().startswith("text="):
        target = target.split("=", 1)[1].strip()
        target = _strip_wrapping_quotes(target)
    if len(target) >= 2 and target[0] == "/" and target[-1] == "/":
        target = target[1:-1]
    selector = target.lower()
    if selector.startswith(("css=", "selector=")):
        selector = selector.split("=", 1)[1].strip()
    if selector in _VISIBLE_SELECTOR_SNAPSHOT_TERMS:
        return _VISIBLE_SELECTOR_SNAPSHOT_TERMS[selector]
    if selector.startswith((".", "#", "[")):
        return None
    return target or None


def _normalize_snapshot_ref_tokens(command: str) -> str:
    """Convert snapshot ref tokens into the target format accepted by playwright-cli.

    Snapshots render elements as `[ref=e12]`, but the CLI command target is the
    bare ref (`e12`). Models naturally copy the visible snapshot token, so the
    executor normalizes that syntax before running commands.
    """
    return _SNAPSHOT_REF_TOKEN.sub(lambda match: match.group("ref"), command)


def _extract_resize_dimensions(command: str) -> tuple[str, str] | None:
    width_match = re.search(r"\bwidth\s*[:=]\s*(?P<w>\d{2,5})", command, flags=re.I)
    height_match = re.search(r"\bheight\s*[:=]\s*(?P<h>\d{2,5})", command, flags=re.I)
    if width_match and height_match:
        return width_match.group("w"), height_match.group("h")
    match = _RESIZE_RE.search(command)
    if not match:
        return None
    width = match.group("w")
    height = match.group("h")
    return width, height


def _normalize_viewport_command(raw: str, normalized: str, name: str) -> dict | None:
    if name == "resize":
        parts = normalized.split(maxsplit=2)
        if len(parts) >= 3 and parts[1].isdigit() and parts[2].isdigit():
            return None
        dimensions = _extract_resize_dimensions(normalized)
        if dimensions:
            width, height = dimensions
            return {
                "command": f"resize {width} {height}",
                "source_command": raw,
                "kind": "normalized",
                "normalization": "Converted resize shorthand to playwright-cli resize <width> <height>.",
            }
        return None

    if name in _VIEWPORT_ALIASES or "setviewportsize" in normalized.replace("_", "").replace("-", "").lower():
        dimensions = _extract_resize_dimensions(normalized)
        if dimensions:
            width, height = dimensions
            return {
                "command": f"resize {width} {height}",
                "source_command": raw,
                "kind": "normalized",
                "normalization": "Converted viewport pseudo-command to playwright-cli resize.",
            }
        return None

    if name in {"evaluate", "eval", "run-code"} and re.search(
        r"(resizeTo|setViewportSize|viewport)", normalized, flags=re.I
    ):
        dimensions = _extract_resize_dimensions(normalized)
        if dimensions:
            width, height = dimensions
            return {
                "command": f"resize {width} {height}",
                "source_command": raw,
                "kind": "normalized",
                "normalization": "Converted viewport JavaScript attempt to playwright-cli resize.",
            }
    return None


def _run_code_body(command: str) -> str | None:
    if command_name(command) != "run-code":
        return None
    parts = strip_playwright_cli_prefix(command).split(maxsplit=1)
    if len(parts) < 2:
        return ""
    return _strip_wrapping_quotes(parts[1])


def _quote_run_code_body(body: str) -> str:
    return json.dumps(body, ensure_ascii=False)


def _run_code_is_diagnostic(body: str) -> bool:
    lowered = body.lower()
    return "console.log" in lowered and not any(
        token in lowered
        for token in (
            "throw ",
            "throw new ",
            "expect(",
            "assert(",
            "page.click",
            ".click(",
            ".fill(",
            "page.goto",
        )
    )


def _normalize_run_code_signature(command: str) -> tuple[str, bool]:
    body = _run_code_body(command)
    if body is None:
        return command, False

    body = re.sub(
        r"async\s*\(\s*\{\s*page\s*\}\s*\)\s*=>",
        "async page =>",
        body,
        count=1,
        flags=re.I,
    )
    advisory = _run_code_is_diagnostic(body)
    if "=>" not in body and not body.lstrip().startswith("function"):
        body = f"async page => {{ {body} }}"
    return f"run-code {_quote_run_code_body(body)}", advisory


def normalize_playwright_command(command: str, include_unsupported: bool = False) -> list[dict]:
    """Normalize generated pseudo-commands into the playwright-cli dialect we execute."""
    raw = command.strip()
    if not raw or raw.startswith("#") or raw.startswith("//") or raw.startswith("```"):
        return []

    normalized = strip_playwright_cli_prefix(raw)
    name = command_name(normalized)
    if not name:
        return []

    viewport_spec = _normalize_viewport_command(raw, normalized, name)
    if viewport_spec:
        return [viewport_spec]

    if name in _WAIT_COMMANDS:
        return [
            {
                "command": "snapshot",
                "source_command": raw,
                "kind": "normalized",
                "normalization": f"Converted unsupported '{name}' command to snapshot.",
            }
        ]

    if name in _ASSERT_COMMANDS:
        expected = _extract_snapshot_assertion(normalized)
        return [
            {
                "command": "snapshot",
                "source_command": raw,
                "kind": "assert_snapshot_contains",
                "expected": expected,
                "normalization": "Converted unsupported assertion command to snapshot evaluation.",
            }
        ]

    if name in _ASSERT_VISIBLE_COMMANDS:
        expected = _extract_visible_assertion(normalized)
        return [
            {
                "command": "snapshot",
                "source_command": raw,
                "kind": "assert_snapshot_contains",
                "expected": expected,
                "normalization": "Converted assert_visible pseudo-command to snapshot visibility assertion.",
            }
        ]

    if name not in SUPPORTED_PLAYWRIGHT_COMMANDS:
        if name == "evaluate":
            return [
                {
                    "command": "eval " + normalized.split(maxsplit=1)[1]
                    if len(normalized.split(maxsplit=1)) > 1
                    else "eval",
                    "source_command": raw,
                    "kind": "normalized",
                    "normalization": "Converted evaluate alias to playwright-cli eval.",
                }
            ]
        if not include_unsupported:
            return []
        return [
            {
                "command": "",
                "source_command": raw,
                "kind": "unsupported",
                "skip": True,
                "normalization": f"Skipped unsupported playwright-cli command '{name}'.",
            }
        ]

    if name == "run-code":
        body = _run_code_body(normalized) or ""
        if _RUN_CODE_REF_LOCATOR.search(body) and _run_code_is_diagnostic(body):
            return [
                {
                    "command": "snapshot",
                    "source_command": raw,
                    "kind": "normalized",
                    "advisory": True,
                    "normalization": (
                        "Converted diagnostic run-code using transient snapshot refs "
                        "to snapshot evidence."
                    ),
                }
            ]

    if name == "screenshot":
        return [
            {
                "command": "screenshot",
                "source_command": raw,
                "kind": "screenshot",
                "normalization": "Replaced screenshot path with run-scoped evidence path.",
            }
        ]

    if name == "run-code":
        executable, advisory = _normalize_run_code_signature(normalized)
    else:
        executable = _normalize_snapshot_ref_tokens(normalized)
        advisory = False
    spec = {"command": executable, "source_command": raw, "kind": "command"}
    if advisory:
        spec["advisory"] = True
    if executable != normalized:
        normalizations = []
        ref_normalized = _normalize_snapshot_ref_tokens(normalized)
        if name == "run-code":
            normalizations.append("Converted run-code JavaScript snippet to playwright-cli page function.")
        elif executable != ref_normalized:
            normalizations.append("Converted run-code ({ page }) signature to playwright-cli page argument.")
        if name != "run-code" and ref_normalized != normalized:
            normalizations.append("Converted snapshot [ref=...] token to playwright-cli element ref.")
        spec["normalization"] = " ".join(normalizations)
    return [spec]


def normalize_playwright_commands(
    commands: list[str],
    include_unsupported: bool = False,
) -> list[dict]:
    normalized: list[dict] = []
    for command in commands:
        normalized.extend(
            normalize_playwright_command(command, include_unsupported=include_unsupported)
        )
    return normalized

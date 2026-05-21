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
_SNAPSHOT_REF_TOKEN = re.compile(r"(?P<quote>['\"]?)\[ref=(?P<ref>[A-Za-z0-9_-]+)\](?P=quote)")


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


def _normalize_snapshot_ref_tokens(command: str) -> str:
    """Convert snapshot ref tokens into the target format accepted by playwright-cli.

    Snapshots render elements as `[ref=e12]`, but the CLI command target is the
    bare ref (`e12`). Models naturally copy the visible snapshot token, so the
    executor normalizes that syntax before running commands.
    """
    return _SNAPSHOT_REF_TOKEN.sub(lambda match: match.group("ref"), command)


def normalize_playwright_command(command: str, include_unsupported: bool = False) -> list[dict]:
    """Normalize generated pseudo-commands into the playwright-cli dialect we execute."""
    raw = command.strip()
    if not raw or raw.startswith("#") or raw.startswith("//") or raw.startswith("```"):
        return []

    normalized = strip_playwright_cli_prefix(raw)
    name = command_name(normalized)
    if not name:
        return []

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

    if name not in SUPPORTED_PLAYWRIGHT_COMMANDS:
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

    if name == "screenshot":
        return [
            {
                "command": "screenshot",
                "source_command": raw,
                "kind": "screenshot",
                "normalization": "Replaced screenshot path with run-scoped evidence path.",
            }
        ]

    executable = _normalize_snapshot_ref_tokens(normalized)
    spec = {"command": executable, "source_command": raw, "kind": "command"}
    if executable != normalized:
        spec["normalization"] = "Converted snapshot [ref=...] token to playwright-cli element ref."
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

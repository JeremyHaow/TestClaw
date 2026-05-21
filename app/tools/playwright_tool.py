import asyncio
import os
import shlex
import subprocess
import tempfile
from pathlib import Path

from langchain_core.tools import tool

from app.config import settings
from app.tools.playwright_commands import normalize_playwright_commands, strip_playwright_cli_prefix


def _find_trace(script_path: str) -> str | None:
    trace_dir = Path(settings.sandbox_dir)
    for candidate in trace_dir.glob("**/trace.zip"):
        if candidate.exists():
            return str(candidate)
    return None


@tool
def execute_playwright_test(code_content: str) -> dict:
    """Execute a Playwright pytest script inside the sandbox and return the result."""
    sandbox_dir = settings.sandbox_dir
    sandbox_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".py",
        dir=sandbox_dir,
        delete=False,
        prefix="test_",
        encoding="utf-8",
    ) as handle:
        handle.write(code_content)
        script_path = handle.name
    try:
        result = subprocess.run(
            [
                "pytest",
                script_path,
                "-v",
                "--tb=short",
            ],
            capture_output=True,
            text=True,
            timeout=settings.SANDBOX_TIMEOUT,
            cwd=sandbox_dir,
        )
        return {
            "status_code": result.returncode,
            "stdout": result.stdout[-3000:],
            "stderr": result.stderr[-3000:],
            "trace_path": _find_trace(script_path),
        }
    except subprocess.TimeoutExpired:
        return {
            "status_code": -1,
            "stdout": "",
            "stderr": "SANDBOX TIMEOUT",
            "trace_path": None,
        }
    except FileNotFoundError as exc:
        return {
            "status_code": -1,
            "stdout": "",
            "stderr": str(exc),
            "trace_path": None,
        }
    finally:
        if os.path.exists(script_path):
            os.unlink(script_path)


async def run_playwright_cli_command(command: str, session: str = "default") -> dict:
    """Execute a single playwright-cli command and return the result."""
    # Remove playwright-cli prefix if present to avoid double prefix
    if command.startswith("playwright-cli "):
        command = command[len("playwright-cli "):]
    sandbox = str(settings.sandbox_dir)
    try:
        proc = await asyncio.create_subprocess_exec(
            "playwright-cli", *shlex.split(command),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=sandbox,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=settings.PLAYWRIGHT_CLI_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            proc.kill()
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=3)
            except Exception:
                stdout, stderr = b"", b""
            return {
                "status_code": -1,
                "stdout": stdout.decode("utf-8", errors="replace").strip(),
                "stderr": (
                    stderr.decode("utf-8", errors="replace").strip()
                    or f"Command timeout after {settings.PLAYWRIGHT_CLI_TIMEOUT_SECONDS}s"
                ),
            }
        stdout_text = stdout.decode("utf-8", errors="replace").strip()
        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        status_code = proc.returncode or 0
        if stdout_text.startswith("### Error"):
            status_code = 1
        return {
            "status_code": status_code,
            "stdout": stdout_text,
            "stderr": stderr_text,
        }
    except FileNotFoundError:
        return {"status_code": -1, "stdout": "", "stderr": "playwright-cli not found. Run: npm install -g @playwright/cli"}
    except Exception as e:
        return {"status_code": -1, "stdout": "", "stderr": str(e)}


async def run_playwright_cli_stream(commands: list[str], session: str = "default"):
    """Execute playwright-cli commands sequentially, yielding results for SSE streaming."""
    for spec in normalize_playwright_commands(commands, include_unsupported=True):
        source_command = spec.get("source_command") or spec.get("command", "")
        if spec.get("skip"):
            yield {
                "command": source_command,
                "normalized_command": None,
                "status": "skipped",
                "status_code": 0,
                "stdout": "",
                "stderr": "",
                "normalization": spec.get("normalization"),
            }
            continue

        executable_command = spec["command"]
        if spec.get("kind") == "screenshot":
            executable_command = strip_playwright_cli_prefix(source_command)
        result = await run_playwright_cli_command(executable_command, session)
        if spec.get("kind") == "assert_snapshot_contains" and spec.get("expected"):
            expected = spec["expected"]
            matched = expected.lower() in result.get("stdout", "").lower()
            if result.get("status_code") == 0 and not matched:
                result = {
                    **result,
                    "status_code": 1,
                    "stderr": f"Snapshot did not contain expected text: {expected}",
                }
        yield {
            "command": source_command,
            "normalized_command": executable_command,
            "normalization": spec.get("normalization"),
            **result,
        }
        # If a command fails critically, stop
        if result["status_code"] != 0 and "not found" in result.get("stderr", "").lower():
            yield {
                "type": "error",
                "data": f"Command failed: {source_command} — {result['stderr']}",
            }
            break


async def run_playwright_cli_script(script_content: str) -> dict:
    """Write a playwright-cli script (line-by-line commands) and execute all lines."""
    lines = [
        line.strip()
        for line in script_content.strip().split("\n")
        if line.strip() and not line.strip().startswith("#")
    ]
    results = []
    async for result in run_playwright_cli_stream(lines):
        if result.get("type") == "error":
            break
        results.append(result)
        if result["status_code"] != 0:
            break
    return {
        "results": results,
        "total": len(results),
        "passed": sum(1 for r in results if r["status_code"] == 0),
    }

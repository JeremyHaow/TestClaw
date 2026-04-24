import asyncio
import json
import os
import subprocess
import tempfile
from pathlib import Path

from langchain_core.tools import tool

from app.config import settings


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
    try:
        proc = await asyncio.create_subprocess_exec(
            "playwright-cli", *command.split(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        return {
            "status_code": proc.returncode or 0,
            "stdout": stdout.decode("utf-8", errors="replace").strip(),
            "stderr": stderr.decode("utf-8", errors="replace").strip(),
        }
    except asyncio.TimeoutError:
        return {"status_code": -1, "stdout": "", "stderr": "Command timeout"}
    except FileNotFoundError:
        return {"status_code": -1, "stdout": "", "stderr": "playwright-cli not found. Run: npm install -g @playwright/cli"}
    except Exception as e:
        return {"status_code": -1, "stdout": "", "stderr": str(e)}


async def run_playwright_cli_stream(commands: list[str], session: str = "default"):
    """Execute playwright-cli commands sequentially, yielding results for SSE streaming."""
    for cmd in commands:
        result = await run_playwright_cli_command(cmd, session)
        yield {"command": cmd, **result}
        # If a command fails critically, stop
        if result["status_code"] != 0 and "not found" in result.get("stderr", "").lower():
            yield {"type": "error", "data": f"Command failed: {cmd} — {result['stderr']}"}
            break


async def run_playwright_cli_script(script_content: str) -> dict:
    """Write a playwright-cli script (line-by-line commands) and execute all lines."""
    lines = [l.strip() for l in script_content.strip().split("\n") if l.strip() and not l.strip().startswith("#")]
    results = []
    for line in lines:
        result = await run_playwright_cli_command(line)
        results.append({"command": line, **result})
        if result["status_code"] != 0:
            break
    return {"results": results, "total": len(results), "passed": sum(1 for r in results if r["status_code"] == 0)}

import json
import logging
import os
import time
from pathlib import Path

from app.agent.state import AgentState
from app.config import settings

logger = logging.getLogger(__name__)


def _ensure_screenshot_dir(task_id: str) -> Path:
    """Ensure screenshot directory exists for this run."""
    screenshot_dir = Path(settings.sandbox_dir) / "screenshots" / task_id
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    return screenshot_dir


def _extract_playwright_commands(ui_cases: list[dict], target_url: str) -> list[str]:
    """Extract playwright-cli commands from UI test cases."""
    commands = []
    url_opened = False

    for case in ui_cases:
        pw_cmds = case.get("playwright_commands", [])
        if pw_cmds:
            for cmd in pw_cmds:
                cmd = cmd.strip()
                if not cmd:
                    continue
                # Ensure we open the right URL first
                if cmd.startswith("open ") and not url_opened:
                    url = cmd[5:].strip()
                    if not url.startswith("http"):
                        url = f"{target_url.rstrip('/')}/{url.lstrip('/')}"
                    commands.append(f"open {url}")
                    url_opened = True
                else:
                    commands.append(cmd)
                    if cmd.startswith("open "):
                        url_opened = True
        else:
            # Generate basic commands from case steps
            if not url_opened:
                commands.append(f"open {target_url}")
                url_opened = True
            commands.append("snapshot")
            for step in case.get("steps", []):
                # Try to interpret steps as actions
                step_lower = step.lower()
                if "click" in step_lower or "点击" in step_lower:
                    # Extract text hint
                    text = step.replace("点击", "").replace("click", "").strip().strip('"').strip("'")
                    if text:
                        commands.append(f'click "{text}"')
                elif "输入" in step_lower or "fill" in step_lower or "type" in step_lower:
                    commands.append(f'type "{step}"')
                elif "查看" in step_lower or "check" in step_lower or "verify" in step_lower:
                    commands.append("snapshot")
                else:
                    commands.append("snapshot")
            commands.append("screenshot")

    # Ensure we always have at least a basic flow
    if not commands:
        commands = [
            f"open {target_url}",
            "snapshot",
            "screenshot",
        ]

    return commands


async def _execute_playwright_commands(commands: list[str], task_id: str, screenshot_dir: Path) -> dict:
    """Execute playwright-cli commands and collect results."""
    from app.tools.playwright_tool import run_playwright_cli_command

    results = []
    screenshots = []
    snapshot_texts = []
    all_passed = True

    for i, cmd in enumerate(commands):
        result = await run_playwright_cli_command(cmd)
        entry = {
            "command": cmd,
            "status_code": result.get("status_code", -1),
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
        }
        results.append(entry)

        # Collect screenshots
        if cmd == "screenshot" or "screenshot" in cmd:
            # Look for screenshot files in sandbox
            sandbox = Path(settings.sandbox_dir)
            for f in sandbox.glob("**/*.png"):
                if f.stat().st_mtime > time.time() - 30:  # recent files
                    dest = screenshot_dir / f"screenshot_{i:03d}.png"
                    try:
                        import shutil
                        shutil.copy2(str(f), str(dest))
                        screenshots.append(str(dest))
                    except Exception:
                        screenshots.append(str(f))

        # Collect snapshot text
        if cmd == "snapshot" and result.get("stdout"):
            snapshot_texts.append(result["stdout"][:2000])

        # Check for assert commands
        if cmd.startswith("assert "):
            if result.get("status_code", -1) != 0:
                all_passed = False
                entry["passed"] = False
            else:
                entry["passed"] = True

        # Stop on critical failure
        if result.get("status_code", -1) != 0 and "not found" in result.get("stderr", "").lower():
            all_passed = False
            break

    return {
        "commands": results,
        "screenshots": screenshots,
        "snapshot_texts": snapshot_texts,
        "total": len(results),
        "passed": sum(1 for r in results if r.get("status_code", -1) == 0),
        "all_passed": all_passed,
    }


async def run(state: AgentState) -> AgentState:
    target_url = state.get("target_url", "")
    ui_seed_url = state.get("ui_seed_url") or target_url
    ui_cases = state.get("ui_cases") or []
    task_id = state.get("task_id", "unknown")

    # Extract commands from UI cases
    commands = _extract_playwright_commands(ui_cases, ui_seed_url)

    # Ensure screenshot directory
    screenshot_dir = _ensure_screenshot_dir(task_id)

    # Execute commands
    exec_result = await _execute_playwright_commands(commands, task_id, screenshot_dir)

    state["ui_execution_result"] = {
        "total": exec_result["total"],
        "passed": exec_result["passed"],
        "failed": exec_result["total"] - exec_result["passed"],
        "pass_rate": f"{round(exec_result['passed'] / exec_result['total'] * 100, 1)}%" if exec_result["total"] else "0%",
        "commands": exec_result["commands"],
        "screenshots": exec_result["screenshots"],
        "snapshot_texts": exec_result["snapshot_texts"],
        "all_passed": exec_result["all_passed"],
    }

    # Store artifacts
    artifacts = state.get("artifacts") or {}
    artifacts["ui_screenshots"] = exec_result["screenshots"]
    artifacts["ui_snapshots"] = exec_result["snapshot_texts"]
    artifacts["ui_commands"] = [
        {"command": r["command"], "status_code": r["status_code"], "stdout": r["stdout"][:500], "stderr": r["stderr"][:500]}
        for r in exec_result["commands"]
    ]
    state["artifacts"] = artifacts

    status = "done" if exec_result["all_passed"] else "failed"
    state.setdefault("workflow_steps", []).append(
        {
            "node": "ui_runner",
            "status": status,
            "detail": f"Executed {exec_result['total']} playwright-cli command(s): {exec_result['passed']} passed",
        }
    )
    return state

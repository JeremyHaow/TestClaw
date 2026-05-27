import hashlib
import json
import logging
import re
import shlex
import time
from pathlib import Path
from urllib.parse import urljoin

from langchain_core.messages import HumanMessage

from app.agent.progress import persist_progress
from app.agent.prompts import UI_EXECUTION_CONTEXT_PROMPT
from app.agent.state import AgentState
from app.agent.tool_registry import install_tool_context, record_tool_call, summarize_tool_calls
from app.config import settings
from app.core.llm_gateway import ainvoke_with_timeout, llm_gateway
from app.tools.playwright_commands import (
    command_name,
    normalize_playwright_commands,
    strip_playwright_cli_prefix,
)

logger = logging.getLogger(__name__)

_ACTION_COMMANDS = {
    "open",
    "goto",
    "click",
    "fill",
    "type",
    "select",
    "hover",
    "press",
    "upload",
    "go-back",
    "reload",
}

_LOGIN_VALIDATION_WORDS = (
    "login success",
    "login failed",
    "wrong password",
    "empty username",
    "empty password",
    "forgot password",
    "captcha",
    "验证码",
    "错误密码",
    "空用户名",
    "空密码",
    "忘记密码",
    "登录成功",
    "登录失败",
)

_AUTHENTICATED_CONTEXT_CATEGORIES = {
    "AUTHENTICATED_HOME",
    "BUSINESS_NAVIGATION",
    "DATA_DISPLAY",
    "FORM",
    "INTERACTION",
    "NAVIGATION",
    "PAGE_LOAD",
}

_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_SNAPSHOT_REF_RE = re.compile(r"\[ref=([A-Za-z0-9_-]+)\]")


def _ensure_screenshot_dir(task_id: str) -> Path:
    """Ensure screenshot directory exists for this run."""
    screenshot_dir = Path(settings.sandbox_dir) / "screenshots" / task_id
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    return screenshot_dir


def _quote_path(path: Path) -> str:
    return f'"{path}"'


def _full_page_screenshot_command(path: Path) -> str:
    return f"screenshot --filename {_quote_path(path)}"


def _smart_wait_command(timeout_ms: int | None = None) -> str:
    timeout = int(timeout_ms or settings.PLAYWRIGHT_SMART_WAIT_MS)
    return (
        'run-code "async page => { '
        f"await page.waitForLoadState('domcontentloaded', {{ timeout: {timeout} }}).catch(() => null); "
        f"await page.waitForLoadState('networkidle', {{ timeout: {timeout} }}).catch(() => null); "
        '}"'
    )


def _strip_url_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _parse_json_object(content: str) -> dict:
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    if not text.startswith("{"):
        match = re.search(r"\{.*\}", text, flags=re.S)
        if match:
            text = match.group(0)
    try:
        parsed = json.loads(text)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _absolutize_navigation_command(command: str, target_url: str) -> str:
    normalized = strip_playwright_cli_prefix(command)
    name = command_name(normalized)
    if name not in {"open", "goto"}:
        return normalized

    parts = normalized.split(maxsplit=1)
    if len(parts) == 1:
        return f"{name} {target_url}" if target_url else normalized

    destination = _strip_url_quotes(parts[1])
    if destination.startswith(("http://", "https://")):
        return f"{name} {destination}"

    base = target_url.rstrip("/") + "/" if target_url else ""
    return f"{name} {urljoin(base, destination.lstrip('/'))}"


def _safe_split_command(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _command_target(command: str) -> str:
    parts = _safe_split_command(strip_playwright_cli_prefix(command))
    return parts[1] if len(parts) > 1 else ""


def _is_ref_token(value: str) -> bool:
    return bool(value and _REF_RE.fullmatch(value) and any(char.isdigit() for char in value))


def _contains_any_text(value: str, words: tuple[str, ...]) -> bool:
    value_lower = value.lower()
    return any(word.lower() in value_lower for word in words)


def _case_text(case: dict, commands: list[str]) -> str:
    parts = [
        str(case.get("title") or ""),
        str(case.get("category") or ""),
        str(case.get("operation_type") or ""),
        " ".join(commands),
    ]
    return " ".join(parts)


def _case_looks_like_login_validation(case: dict, commands: list[str]) -> bool:
    text = _case_text(case, commands)
    if _contains_any_text(text, _LOGIN_VALIDATION_WORDS):
        return True
    category = str(case.get("category") or "").upper()
    if category in {"AUTH", "LOGIN"}:
        return True
    return False


def _case_should_use_authenticated_context(
    case: dict,
    commands: list[str],
    has_authenticated_context: bool,
) -> bool:
    if not has_authenticated_context:
        return False

    if "_testclaw_use_prepared_context" in case:
        return bool(case.get("_testclaw_use_prepared_context"))

    explicit = case.get("requires_authenticated_context")
    if explicit is True:
        return True
    if explicit is False:
        return False

    if _case_looks_like_login_validation(case, commands):
        return False

    if case.get("source") == "authenticated_snapshot" or case.get("operation_type"):
        return True
    if case.get("target_action"):
        return True

    category = str(case.get("category") or "").upper()
    if category in _AUTHENTICATED_CONTEXT_CATEGORIES:
        return True

    return True


def _command_is_target_navigation(command: str, target_url: str) -> bool:
    if command_name(command) not in {"open", "goto"}:
        return False
    target = _strip_url_quotes(_command_target(command)).rstrip("/")
    return bool(target_url and target == target_url.rstrip("/"))


def _command_looks_like_login_form_step(command: str) -> bool:
    name = command_name(command)
    if name in {"fill", "type", "press"}:
        return True
    if name == "click" and _is_ref_token(_command_target(command)):
        return True
    if name in {"snapshot", "screenshot"}:
        return True
    return False


def _strip_redundant_login_setup_commands(commands: list[str], target_url: str) -> list[str]:
    """Remove old login-form commands before replaying a prepared auth context."""
    has_login_sequence = any(_command_is_target_navigation(command, target_url) for command in commands) or any(
        command_name(command) in {"fill", "type"} for command in commands
    )
    if not has_login_sequence:
        return _strip_leading_navigation(commands)

    stripped: list[str] = []
    dropping = True
    for command in commands:
        if dropping and (
            _command_is_target_navigation(command, target_url)
            or _command_looks_like_login_form_step(command)
        ):
            continue
        dropping = False
        stripped.append(command)

    return stripped or ["snapshot", "screenshot"]


def _command_payload_for_analysis(case: dict) -> dict:
    return {
        "title": case.get("title"),
        "category": case.get("category"),
        "source": case.get("source"),
        "requires_authenticated_context": case.get("requires_authenticated_context"),
        "steps": case.get("steps"),
        "expected": case.get("expected"),
        "playwright_commands": case.get("playwright_commands"),
        "raw_playwright_commands": case.get("raw_playwright_commands"),
    }


def _fallback_ui_execution_context_plan(
    ui_cases: list[dict],
    *,
    prepared_context_available: bool,
    source_input: str | None = None,
) -> list[dict]:
    selected_suite = str(source_input or "").strip().lower() == "suite"
    decisions: list[dict] = []
    for index, case in enumerate(ui_cases):
        commands = [
            command
            for command in (case.get("raw_playwright_commands") or case.get("playwright_commands") or [])
            if isinstance(command, str)
        ]
        explicit = case.get("requires_authenticated_context")
        looks_like_setup_validation = _case_looks_like_login_validation(case, commands)
        if explicit is True:
            use_prepared_context = prepared_context_available
        elif explicit is False or looks_like_setup_validation:
            use_prepared_context = False
        elif selected_suite:
            use_prepared_context = False
        else:
            use_prepared_context = prepared_context_available
        decisions.append(
            {
                "case_index": index,
                "use_prepared_context": use_prepared_context,
                "strip_preparation_steps": use_prepared_context,
                "intent": "prepared_context_flow" if use_prepared_context else "fresh_entry_flow",
                "reason": (
                    "Generic fallback selected prepared context because setup was verified."
                    if use_prepared_context
                    else "Generic fallback kept the case on its original entry context."
                ),
                "source": "fallback",
            }
        )
    return decisions


async def _analyze_ui_execution_context(state: AgentState, ui_cases: list[dict]) -> list[dict]:
    prepared_context_available = bool(
        (state.get("setup_result") or state.get("login_result") or {}).get("required")
        and state.get("login_verified") is True
    )
    if not ui_cases:
        return []

    fallback = _fallback_ui_execution_context_plan(
        ui_cases,
        prepared_context_available=prepared_context_available,
        source_input=state.get("source_input"),
    )
    if not prepared_context_available:
        state["ui_execution_context_plan"] = fallback
        return fallback

    db = state.get("db_session")
    if db is None:
        state["ui_execution_context_plan"] = fallback
        return fallback

    try:
        llm = await llm_gateway.get_planner(db)
        auth_context = state.get("authenticated_ui_context") or {}
        prompt = UI_EXECUTION_CONTEXT_PROMPT.format(
            target_url=state.get("ui_seed_url") or state.get("target_url", ""),
            setup_instructions=state.get("setup_instructions") or state.get("login_instructions") or "",
            login_verified=state.get("login_verified"),
            post_setup_url=auth_context.get("post_login_url") or "",
            post_setup_snapshot=(state.get("ui_login_snapshot") or "")[:6000],
            ui_cases=json.dumps(
                [_command_payload_for_analysis(case) for case in ui_cases],
                ensure_ascii=False,
                default=str,
            )[:8000],
        )
        resp = await ainvoke_with_timeout(
            llm,
            [HumanMessage(content=prompt)],
            call_name="ui_runner.plan_execution_context",
        )
        content = resp.content if hasattr(resp, "content") else str(resp)
        parsed = _parse_json_object(str(content))
        raw_decisions = parsed.get("decisions")
        decisions_by_index = {
            int(item.get("case_index")): item
            for item in raw_decisions
            if isinstance(item, dict) and str(item.get("case_index", "")).isdigit()
        } if isinstance(raw_decisions, list) else {}
        decisions: list[dict] = []
        for fallback_decision in fallback:
            index = int(fallback_decision["case_index"])
            decision = decisions_by_index.get(index)
            if not isinstance(decision, dict):
                decisions.append(fallback_decision)
                continue
            use_prepared_context = bool(decision.get("use_prepared_context")) and prepared_context_available
            decisions.append(
                {
                    "case_index": index,
                    "use_prepared_context": use_prepared_context,
                    "strip_preparation_steps": bool(decision.get("strip_preparation_steps")) and use_prepared_context,
                    "intent": str(decision.get("intent") or fallback_decision["intent"])[:200],
                    "reason": str(decision.get("reason") or fallback_decision["reason"])[:500],
                    "source": "llm",
                }
            )

        record_tool_call(
            state,
            tool_name="planner.analyze_ui_execution_context",
            layer="planner",
            status="success",
            input_summary={
                "case_count": len(ui_cases),
                "prepared_context_available": prepared_context_available,
            },
            output_summary={
                "prepared_context_cases": sum(1 for item in decisions if item.get("use_prepared_context")),
                "fresh_entry_cases": sum(1 for item in decisions if not item.get("use_prepared_context")),
            },
        )
        state["ui_execution_context_plan"] = decisions
        return decisions
    except Exception as exc:
        logger.warning("UI execution context analysis failed: %s", exc)
        record_tool_call(
            state,
            tool_name="planner.analyze_ui_execution_context",
            layer="planner",
            status="failed",
            input_summary={
                "case_count": len(ui_cases),
                "prepared_context_available": prepared_context_available,
            },
            output_summary={"fallback": True, "error": str(exc)[:300]},
        )
        state["ui_execution_context_plan"] = fallback
        return fallback


def _apply_ui_execution_context_plan(ui_cases: list[dict], decisions: list[dict]) -> list[dict]:
    decisions_by_index = {
        int(item.get("case_index")): item
        for item in decisions
        if isinstance(item, dict) and str(item.get("case_index", "")).isdigit()
    }
    annotated: list[dict] = []
    for index, case in enumerate(ui_cases):
        next_case = dict(case)
        decision = decisions_by_index.get(index)
        if decision:
            next_case["_testclaw_use_prepared_context"] = bool(decision.get("use_prepared_context"))
            next_case["_testclaw_strip_preparation_steps"] = bool(decision.get("strip_preparation_steps"))
            next_case["_testclaw_execution_context_reason"] = decision.get("reason")
            next_case["_testclaw_execution_context_source"] = decision.get("source")
        annotated.append(next_case)
    return annotated


def _extract_text_from_snapshot_line(line: str) -> str:
    quoted = re.search(r'"([^"]+)"', line)
    if quoted:
        return quoted.group(1).strip()
    before_ref = line.split("[ref=", 1)[0]
    cleaned = re.sub(r"^\s*-\s*\w+\s*", "", before_ref)
    return re.sub(r"\s+", " ", cleaned.strip(": -")).strip()


def _find_ref_by_text(snapshot: str, text: str, *, control_only: bool = False) -> str | None:
    expected = re.sub(r"\s+", " ", text or "").strip().lower()
    if not expected:
        return None

    candidates: list[tuple[int, str]] = []
    for line in (snapshot or "").splitlines():
        ref_match = _SNAPSHOT_REF_RE.search(line)
        if not ref_match:
            continue
        line_lower = line.lower()
        if control_only and not re.search(r"\b(textbox|searchbox|combobox|spinbutton)\b", line_lower):
            continue
        label = _extract_text_from_snapshot_line(line).lower()
        if not label:
            continue
        if label == expected:
            score = 0
        elif expected in label:
            score = 1
        elif label in expected:
            score = 2
        else:
            continue
        if "[cursor=pointer]" in line:
            score -= 1
        candidates.append((score, ref_match.group(1)))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _find_single_clickable_ref(snapshot: str) -> str | None:
    refs: list[str] = []
    for line in (snapshot or "").splitlines():
        if "[cursor=pointer]" not in line:
            continue
        if not re.search(r"\b(link|button)\b", line.lower()):
            continue
        ref_match = _SNAPSHOT_REF_RE.search(line)
        if ref_match:
            refs.append(ref_match.group(1))
    return refs[0] if len(set(refs)) == 1 else None


async def _resolve_semantic_command(command: str) -> tuple[str, dict | None]:
    """Resolve model-friendly text targets to current snapshot refs when possible."""
    name = command_name(command)
    if name not in {"click", "fill", "select"}:
        return command, None

    parts = _safe_split_command(strip_playwright_cli_prefix(command))
    if len(parts) < 2 or _is_ref_token(parts[1]):
        return command, None

    target_text = parts[1]
    if not target_text or target_text.startswith(("#", ".", "//", "xpath=")):
        return command, None

    from app.tools.playwright_tool import run_playwright_cli_command

    snapshot_result = await run_playwright_cli_command("snapshot")
    if snapshot_result.get("status_code", -1) != 0:
        return command, {
            "resolved": False,
            "reason": "snapshot failed before semantic command resolution",
            "target": target_text,
        }

    ref = _find_ref_by_text(
        snapshot_result.get("stdout", ""),
        target_text,
        control_only=name in {"fill", "select"},
    )
    fallback_reason = None
    if not ref and name == "click":
        ref = _find_single_clickable_ref(snapshot_result.get("stdout", ""))
        if ref:
            fallback_reason = "single clickable target fallback"
    if not ref:
        return command, {
            "resolved": False,
            "reason": "no matching ref in current snapshot",
            "target": target_text,
        }

    if name == "click":
        resolved = f"click {ref}"
    elif name == "fill" and len(parts) >= 3:
        value = " ".join(shlex.quote(part) for part in parts[2:])
        resolved = f"fill {ref} {value}"
    elif name == "select" and len(parts) >= 3:
        value = " ".join(shlex.quote(part) for part in parts[2:])
        resolved = f"select {ref} {value}"
    else:
        resolved = command

    if resolved == command:
        return command, None
    result = {"resolved": True, "target": target_text, "ref": ref}
    if fallback_reason:
        result["reason"] = fallback_reason
    return resolved, result


def _build_commands_from_steps(
    case: dict,
    target_url: str,
    authenticated_context_available: bool = False,
) -> list[str]:
    requires_auth_context = bool(case.get("requires_authenticated_context")) and authenticated_context_available
    commands = (
        [f"open {target_url}", "snapshot"]
        if target_url and not requires_auth_context
        else ["snapshot"]
    )
    for step in case.get("steps", []):
        step_lower = str(step).lower()
        if "click" in step_lower or "点击" in step_lower:
            text = str(step).replace("点击", "").replace("click", "").strip().strip('"').strip("'")
            if text:
                commands.append(f'click "{text}"')
        elif "输入" in step_lower or "fill" in step_lower or "type" in step_lower:
            commands.append(f'type "{step}"')
        elif "查看" in step_lower or "check" in step_lower or "verify" in step_lower:
            commands.append("snapshot")
        else:
            commands.append("snapshot")
        commands.append("screenshot")
    return commands


def _case_raw_commands(
    case: dict,
    target_url: str,
    authenticated_context_available: bool = False,
) -> list[str]:
    commands = []
    requires_auth_context = bool(case.get("requires_authenticated_context")) and authenticated_context_available
    command_source = case.get("raw_playwright_commands") or case.get("playwright_commands") or []
    for command in command_source:
        if not isinstance(command, str):
            continue
        command = command.strip()
        if not command:
            continue
        commands.append(_absolutize_navigation_command(command, target_url))

    if not commands:
        commands = _build_commands_from_steps(case, target_url, authenticated_context_available)

    has_navigation = any(command_name(command) in {"open", "goto"} for command in commands)
    if target_url and not requires_auth_context and not has_navigation:
        commands.insert(0, f"open {target_url}")

    has_screenshot = any(command_name(command) == "screenshot" for command in commands)
    if not has_screenshot:
        commands.append("screenshot")

    return commands


def _strip_leading_navigation(commands: list[str]) -> list[str]:
    stripped = list(commands)
    while stripped and command_name(stripped[0]) in {"open", "goto"}:
        stripped = stripped[1:]
    return stripped


def _next_executable_is_screenshot(specs: list[dict], start_index: int) -> bool:
    for candidate in specs[start_index:]:
        if candidate.get("skip"):
            continue
        return candidate.get("kind") == "screenshot" or command_name(
            candidate.get("command", "")
        ) == "screenshot"
    return False


def _next_executable_is_smart_wait(specs: list[dict], start_index: int) -> bool:
    for candidate in specs[start_index:]:
        if candidate.get("skip"):
            continue
        return candidate.get("kind") == "smart_wait" or command_name(
            candidate.get("command", "")
        ) == "run-code"
    return False


def _with_action_evidence_screenshots(specs: list[dict]) -> list[dict]:
    """Add runner-owned screenshots after action commands when generated cases omit them."""
    enriched: list[dict] = []
    for index, spec in enumerate(specs):
        enriched.append(spec)
        if spec.get("skip") or spec.get("kind") == "screenshot":
            continue
        name = command_name(spec.get("command", ""))
        if name not in _ACTION_COMMANDS:
            continue
        if name == "open" and spec.get("command", "").strip().lower() == "open about:blank":
            continue
        if not _next_executable_is_smart_wait(specs, index + 1):
            enriched.append(
                {
                    "command": _smart_wait_command(),
                    "source_command": f"smart wait after {name}",
                    "kind": "smart_wait",
                    "normalization": f"Added smart wait after '{name}' command.",
                }
            )
        if _next_executable_is_screenshot(specs, index + 1):
            continue
        enriched.append(
            {
                "command": "screenshot",
                "source_command": f"auto screenshot after {name}",
                "kind": "screenshot",
                "normalization": f"Added screenshot evidence after '{name}' command.",
            }
        )
    return enriched


def _command_action_label(command: str) -> str:
    name = command_name(command)
    if name in {"open", "goto"}:
        return "打开页面后"
    if name == "state-load":
        return "恢复登录状态后"
    if name == "click":
        return "点击操作后"
    if name in {"fill", "type"}:
        return "输入测试数据后"
    if name == "select":
        return "选择下拉选项后"
    if name in {"check", "uncheck"}:
        return "勾选状态变更后"
    if name == "reload":
        return "刷新页面后"
    if name == "dialog-dismiss":
        return "取消确认弹窗后"
    if name == "dialog-accept":
        return "确认弹窗后"
    if name == "snapshot":
        return "读取页面结构后"
    return "执行操作后"


def _last_executed_action(command_results: list[dict]) -> dict | None:
    for entry in reversed(command_results):
        if entry.get("status") == "skipped":
            continue
        command = str(entry.get("normalized_command") or entry.get("command") or "")
        if command_name(command) == "screenshot":
            continue
        return entry
    return None


def _ui_tool_name(spec: dict, command: str) -> str:
    if spec.get("kind") == "smart_wait":
        return "ui.smart_wait"
    if spec.get("kind") == "assert_snapshot_contains":
        return "ui.snapshot_assert"
    return "ui.playwright_cli"


def _screenshot_content_hash(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _screenshot_evidence(
    *,
    path: str,
    case_index: int,
    case_title: str,
    step_index: int,
    source_command: str,
    previous_action: dict | None,
) -> dict:
    previous_command = ""
    if previous_action:
        previous_command = str(previous_action.get("normalized_command") or previous_action.get("command") or "")
    action_label = _command_action_label(previous_command) if previous_command else "截图证据"
    detail = previous_command or source_command
    return {
        "path": path,
        "filename": Path(path).name,
        "case_index": case_index,
        "case_title": case_title,
        "step": step_index + 1,
        "step_index": step_index,
        "label": action_label,
        "title": f"{case_title} - {action_label}",
        "detail": detail,
        "action": detail,
        "after_command": previous_command,
        "source_command": source_command,
    }


def _build_ui_case_batches(
    ui_cases: list[dict],
    target_url: str,
    authenticated_setup_commands: list[str] | None = None,
) -> list[dict]:
    if not ui_cases:
        ui_cases = [
            {
                "title": "页面可访问性与基础渲染检查",
                "steps": ["打开目标页面", "获取页面快照", "保存页面截图"],
                "expected": ["页面正常加载"],
                "priority": "P1",
                "category": "PAGE_LOAD",
                "case_type": "ui",
                "playwright_commands": [f"open {target_url}", "snapshot", "screenshot"],
            }
        ]

    batches = []
    for index, case in enumerate(ui_cases):
        title = case.get("title") or f"UI Case {index + 1}"
        has_authenticated_context = bool(authenticated_setup_commands)
        command_source = case.get("raw_playwright_commands") or case.get("playwright_commands") or []
        source_commands = [command for command in command_source if isinstance(command, str)]
        authenticated_context_available = _case_should_use_authenticated_context(
            case,
            source_commands,
            has_authenticated_context,
        )
        raw_commands = _case_raw_commands(case, target_url, authenticated_context_available)
        if authenticated_context_available and authenticated_setup_commands:
            if case.get("_testclaw_strip_preparation_steps") is False:
                raw_commands = _strip_leading_navigation(raw_commands)
            else:
                raw_commands = _strip_redundant_login_setup_commands(raw_commands, target_url)
            raw_commands = [*authenticated_setup_commands, *raw_commands]
        normalized = normalize_playwright_commands(raw_commands, include_unsupported=True)
        normalized = _with_action_evidence_screenshots(normalized)
        if not any(not spec.get("skip") for spec in normalized):
            normalized = normalize_playwright_commands(
                [f"open {target_url}", "snapshot", "screenshot"],
                include_unsupported=True,
            )
            normalized = _with_action_evidence_screenshots(normalized)
        batches.append(
            {
                "case_index": index,
                "case_title": title,
                "case": case,
                "raw_commands": raw_commands,
                "commands": normalized,
            }
        )
    return batches


def _build_ui_execution_result(
    case_results: list[dict],
    command_results: list[dict],
    screenshots: list[str],
    screenshot_evidence: list[dict],
    snapshot_texts: list[str],
    total_cases: int,
    planned_commands: int,
    normalization_warnings: list[dict],
    complete: bool,
) -> dict:
    completed_cases = len(case_results)
    passed_cases = sum(1 for result in case_results if result.get("passed"))
    failed_cases = completed_cases - passed_cases
    command_completed = sum(1 for result in command_results if result.get("status") != "skipped")
    command_passed = sum(
        1
        for result in command_results
        if result.get("status") != "skipped" and result.get("status_code", -1) == 0
    )
    all_passed = (
        complete
        and completed_cases == total_cases
        and failed_cases == 0
        and command_completed == planned_commands
    )

    return {
        "total": total_cases,
        "completed": completed_cases,
        "passed": passed_cases,
        "failed": failed_cases,
        "pending": max(total_cases - completed_cases, 0),
        "pass_rate": (
            f"{round(passed_cases / completed_cases * 100, 1)}%" if completed_cases else "0%"
        ),
        "all_passed": all_passed,
        "complete": complete,
        "case_total": total_cases,
        "cases": case_results,
        "command_total": planned_commands,
        "command_completed": command_completed,
        "command_passed": command_passed,
        "command_failed": command_completed - command_passed,
        "commands": command_results,
        "screenshots": screenshots,
        "screenshot_evidence": screenshot_evidence,
        "snapshot_texts": snapshot_texts,
        "normalization_warnings": normalization_warnings,
    }


async def _execute_ui_case_batches(
    batches: list[dict],
    task_id: str,
    screenshot_dir: Path,
    state: AgentState,
) -> dict:
    """Execute UI cases independently and collect per-case screenshot evidence."""
    from app.tools.playwright_tool import run_playwright_cli_command
    from app.services.screenshot_storage import store_screenshot

    case_results: list[dict] = []
    command_results: list[dict] = []
    screenshots: list[str] = []
    screenshot_evidence: list[dict] = []
    snapshot_texts: list[str] = []
    normalization_warnings: list[dict] = []
    seen_screenshot_hashes: dict[str, dict] = {}
    total_cases = len(batches)
    planned_commands = sum(
        1 for batch in batches for spec in batch["commands"] if not spec.get("skip")
    )

    state["ui_execution_result"] = _build_ui_execution_result(
        case_results,
        command_results,
        screenshots,
        screenshot_evidence,
        snapshot_texts,
        total_cases,
        planned_commands,
        normalization_warnings,
        complete=False,
    )
    await persist_progress(
        state,
        "ui_runner",
        "running",
        f"Executing {total_cases} UI case(s)",
    )

    for batch in batches:
        case_index = batch["case_index"]
        case_title = batch["case_title"]
        case_command_results: list[dict] = []
        case_screenshots: list[str] = []
        case_screenshot_evidence: list[dict] = []
        case_snapshot_texts: list[str] = []
        case_passed = True

        await persist_progress(
            state,
            "ui_runner",
            "running",
            f"Executing UI case {case_index + 1}/{total_cases}: {case_title}",
        )

        for step_index, spec in enumerate(batch["commands"]):
            source_command = spec.get("source_command") or spec.get("command", "")
            if spec.get("normalization"):
                normalization_warnings.append(
                    {
                        "case_index": case_index,
                        "case_title": case_title,
                        "source_command": source_command,
                        "detail": spec["normalization"],
                    }
                )

            if spec.get("skip"):
                record_tool_call(
                    state,
                    tool_name="ui.playwright_cli",
                    layer="ui",
                    status="skipped",
                    input_summary={"command": source_command},
                    output_summary={"reason": spec.get("normalization")},
                    case_index=case_index,
                    case_title=case_title,
                )
                entry = {
                    "case_index": case_index,
                    "case_title": case_title,
                    "command": source_command,
                    "normalized_command": None,
                    "status": "skipped",
                    "status_code": 0,
                    "stdout": "",
                    "stderr": "",
                    "passed": True,
                    "normalization": spec.get("normalization"),
                }
                command_results.append(entry)
                case_command_results.append(entry)
                continue

            normalized_command = spec["command"]
            screenshot_path = None
            previous_action = None
            if spec.get("kind") == "screenshot":
                screenshot_path = screenshot_dir / (
                    f"case_{case_index:03d}_step_{step_index:03d}_shot_"
                    f"{len(case_screenshots) + 1:03d}.png"
                )
                normalized_command = _full_page_screenshot_command(screenshot_path)
                previous_action = _last_executed_action(case_command_results)
            elif spec.get("kind") == "command":
                resolved_command, resolution = await _resolve_semantic_command(normalized_command)
                if resolution:
                    record_tool_call(
                        state,
                        tool_name="ui.playwright_cli",
                        layer="ui",
                        status="success" if resolution.get("resolved") else "failed",
                        input_summary={
                            "command": normalized_command,
                            "source_command": source_command,
                            "phase": "semantic_resolution",
                        },
                        output_summary=resolution,
                        case_index=case_index,
                        case_title=case_title,
                    )
                    if resolution.get("resolved"):
                        normalized_command = resolved_command
                        normalization_warnings.append(
                            {
                                "case_index": case_index,
                                "case_title": case_title,
                                "source_command": source_command,
                                "detail": (
                                    f"Resolved semantic target '{resolution.get('target')}' "
                                    f"to snapshot ref {resolution.get('ref')}."
                                ),
                            }
                        )

            start = time.perf_counter()
            result = await run_playwright_cli_command(normalized_command)
            elapsed = round((time.perf_counter() - start) * 1000, 2)
            status_code = result.get("status_code", -1)
            stdout = result.get("stdout", "")
            stderr = result.get("stderr", "")
            passed = status_code == 0

            if spec.get("kind") == "assert_snapshot_contains":
                expected = spec.get("expected")
                if stdout:
                    snapshot_texts.append(stdout[:2000])
                    case_snapshot_texts.append(stdout[:2000])
                if expected:
                    passed = status_code == 0 and expected.lower() in stdout.lower()
                    if not passed and status_code == 0:
                        status_code = 1
                        stderr = f"Snapshot did not contain expected text: {expected}"
            elif command_name(normalized_command) == "snapshot" and stdout:
                snapshot_texts.append(stdout[:2000])
                case_snapshot_texts.append(stdout[:2000])

            entry = {
                "case_index": case_index,
                "case_title": case_title,
                "command": source_command,
                "normalized_command": normalized_command,
                "status": "executed",
                "status_code": status_code,
                "stdout": stdout,
                "stderr": stderr,
                "passed": passed,
            }
            if spec.get("normalization"):
                entry["normalization"] = spec["normalization"]
            if spec.get("expected"):
                entry["assertion"] = {
                    "type": "snapshot_contains",
                    "expected": spec["expected"],
                    "passed": passed,
                }

            record_tool_call(
                state,
                tool_name=_ui_tool_name(spec, normalized_command),
                layer="ui",
                status="success" if passed else "failed",
                input_summary={
                    "command": normalized_command,
                    "source_command": source_command,
                },
                output_summary={
                    "status_code": status_code,
                    "stdout_chars": len(stdout),
                    "stderr": stderr[:300] if stderr else "",
                },
                elapsed_ms=elapsed,
                case_index=case_index,
                case_title=case_title,
            )

            if screenshot_path is not None:
                if screenshot_path.exists():
                    screenshot_value = str(screenshot_path)
                    evidence = _screenshot_evidence(
                        path=screenshot_value,
                        case_index=case_index,
                        case_title=case_title,
                        step_index=step_index,
                        source_command=source_command,
                        previous_action=previous_action,
                    )
                    content_hash = _screenshot_content_hash(screenshot_path)
                    if content_hash:
                        evidence["content_hash"] = content_hash
                    duplicate = seen_screenshot_hashes.get(content_hash or "")
                    if duplicate:
                        evidence["is_duplicate"] = True
                        evidence["duplicate_of"] = duplicate.get("filename")
                        evidence["storage"] = duplicate.get("storage", {})
                        if duplicate.get("url"):
                            evidence["url"] = duplicate["url"]
                    else:
                        storage = await store_screenshot(screenshot_path, task_id)
                        evidence["storage"] = storage
                        if storage.get("url"):
                            evidence["url"] = storage["url"]
                        if content_hash:
                            seen_screenshot_hashes[content_hash] = evidence
                    screenshots.append(screenshot_value)
                    case_screenshots.append(screenshot_value)
                    screenshot_evidence.append(evidence)
                    case_screenshot_evidence.append(evidence)
                    entry["screenshot"] = screenshot_value
                    entry["screenshot_evidence"] = evidence
                    entry["evidence_label"] = evidence["label"]
                    entry["evidence_detail"] = evidence["detail"]
                else:
                    passed = False
                    entry["passed"] = False
                    entry["status_code"] = 1
                    entry["stderr"] = (
                        f"{stderr}\nScreenshot file was not created: {screenshot_path}".strip()
                    )

            if not passed:
                case_passed = False

            command_results.append(entry)
            case_command_results.append(entry)

            if entry["status_code"] != 0 and "not found" in entry.get("stderr", "").lower():
                break

        if not case_screenshots:
            final_path = screenshot_dir / f"case_{case_index:03d}_final.png"
            final_command = _full_page_screenshot_command(final_path)
            start = time.perf_counter()
            result = await run_playwright_cli_command(final_command)
            elapsed = round((time.perf_counter() - start) * 1000, 2)
            passed = result.get("status_code", -1) == 0 and final_path.exists()
            record_tool_call(
                state,
                tool_name="ui.playwright_cli",
                layer="ui",
                status="success" if passed else "failed",
                input_summary={"command": final_command, "source_command": "final screenshot"},
                output_summary={
                    "status_code": result.get("status_code", -1),
                    "screenshot_created": final_path.exists(),
                },
                elapsed_ms=elapsed,
                case_index=case_index,
                case_title=case_title,
            )
            if not passed:
                case_passed = False
            else:
                screenshot_value = str(final_path)
                evidence = _screenshot_evidence(
                    path=screenshot_value,
                    case_index=case_index,
                    case_title=case_title,
                    step_index=len(case_command_results),
                    source_command="final screenshot",
                    previous_action=_last_executed_action(case_command_results),
                )
                content_hash = _screenshot_content_hash(final_path)
                if content_hash:
                    evidence["content_hash"] = content_hash
                duplicate = seen_screenshot_hashes.get(content_hash or "")
                if duplicate:
                    evidence["is_duplicate"] = True
                    evidence["duplicate_of"] = duplicate.get("filename")
                    evidence["storage"] = duplicate.get("storage", {})
                    if duplicate.get("url"):
                        evidence["url"] = duplicate["url"]
                else:
                    storage = await store_screenshot(final_path, task_id)
                    evidence["storage"] = storage
                    if storage.get("url"):
                        evidence["url"] = storage["url"]
                    if content_hash:
                        seen_screenshot_hashes[content_hash] = evidence
                screenshots.append(screenshot_value)
                case_screenshots.append(screenshot_value)
                screenshot_evidence.append(evidence)
                case_screenshot_evidence.append(evidence)

            entry = {
                "case_index": case_index,
                "case_title": case_title,
                "command": "screenshot",
                "normalized_command": final_command,
                "status": "executed",
                "status_code": 0 if passed else 1,
                "stdout": result.get("stdout", ""),
                "stderr": result.get("stderr", ""),
                "passed": passed,
                "screenshot": str(final_path) if final_path.exists() else None,
                "normalization": "Added final case screenshot for evidence.",
            }
            if final_path.exists() and case_screenshot_evidence:
                entry["screenshot_evidence"] = case_screenshot_evidence[-1]
                entry["evidence_label"] = case_screenshot_evidence[-1]["label"]
                entry["evidence_detail"] = case_screenshot_evidence[-1]["detail"]
            command_results.append(entry)
            case_command_results.append(entry)

        case_result = {
            "case_index": case_index,
            "title": case_title,
            "status": "passed" if case_passed else "failed",
            "passed": case_passed,
            "total_commands": len([spec for spec in batch["commands"] if not spec.get("skip")]),
            "completed_commands": len(
                [result for result in case_command_results if result.get("status") != "skipped"]
            ),
            "screenshots": case_screenshots,
            "screenshot_evidence": case_screenshot_evidence,
            "snapshot_texts": case_snapshot_texts,
            "commands": case_command_results,
        }
        case_results.append(case_result)

        state["ui_execution_result"] = _build_ui_execution_result(
            case_results,
            command_results,
            screenshots,
            screenshot_evidence,
            snapshot_texts,
            total_cases,
            planned_commands,
            normalization_warnings,
            complete=False,
        )
        await persist_progress(
            state,
            "ui_runner",
            "running",
            f"Completed UI case {case_index + 1}/{total_cases}: {case_title}",
        )

    return _build_ui_execution_result(
        case_results,
        command_results,
        screenshots,
        screenshot_evidence,
        snapshot_texts,
        total_cases,
        planned_commands,
        normalization_warnings,
        complete=True,
    )


async def run(state: AgentState) -> AgentState:
    install_tool_context(state)
    state["agent_execution_stage"] = "ui"
    target_url = state.get("target_url", "")
    ui_seed_url = state.get("ui_seed_url") or target_url
    ui_cases = state.get("ui_cases") or []
    task_id = state.get("task_id", "unknown")
    setup_required = bool((state.get("setup_instructions") or state.get("login_instructions") or "").strip())
    login_verified = state.get("login_verified")
    setup_result = state.get("setup_result") or state.get("login_result") or {}

    if setup_required and setup_result.get("required") and login_verified is False:
        detail = state.get("login_verification_reason") or "Pre-test setup verification failed; UI execution was skipped"
        exec_result = {
            "total": len(ui_cases),
            "completed": 0,
            "passed": 0,
            "failed": len(ui_cases) if ui_cases else 1,
            "pending": 0,
            "pass_rate": "0%",
            "all_passed": False,
            "complete": True,
            "case_total": len(ui_cases),
            "cases": [
                {
                    "case_index": index,
                    "title": case.get("title") or f"UI Case {index + 1}",
                    "status": "skipped",
                    "passed": False,
                    "skip_reason": detail,
                    "screenshots": [],
                    "snapshot_texts": [],
                    "commands": [],
                }
                for index, case in enumerate(ui_cases)
            ],
            "command_total": 0,
            "command_completed": 0,
            "command_passed": 0,
            "command_failed": 0,
            "commands": [
                {
                    "case_index": index,
                    "case_title": case.get("title") or f"UI Case {index + 1}",
                    "command": "authenticated_ui_execution",
                    "normalized_command": None,
                    "status": "skipped",
                    "status_code": 1,
                    "stdout": "",
                    "stderr": detail,
                    "passed": False,
                }
                for index, case in enumerate(ui_cases)
            ],
            "screenshots": [],
            "screenshot_evidence": [],
            "snapshot_texts": [],
            "normalization_warnings": [],
            "skip_reason": detail,
        }
        state["ui_execution_result"] = exec_result
        artifacts = state.get("artifacts") or {}
        artifacts["ui_screenshots"] = []
        artifacts["ui_screenshot_evidence"] = []
        artifacts["ui_snapshots"] = []
        artifacts["ui_case_evidence"] = [
            {
                "case_index": case["case_index"],
                "title": case["title"],
                "status": case["status"],
                "screenshots": case["screenshots"],
                "screenshot_evidence": case.get("screenshot_evidence", []),
                "skip_reason": detail,
            }
            for case in exec_result["cases"]
        ]
        artifacts["ui_normalization_warnings"] = []
        artifacts["ui_commands"] = exec_result["commands"]
        state["tool_summary"] = summarize_tool_calls(state.get("tool_calls"))
        artifacts["tool_calls"] = state.get("tool_calls", [])
        artifacts["tool_summary"] = state["tool_summary"]
        state["artifacts"] = artifacts
        state.setdefault("workflow_steps", []).append(
            {"node": "ui_runner", "status": "failed", "detail": detail}
        )
        await persist_progress(state, "ui_runner", "failed", detail)
        return state

    auth_context = state.get("authenticated_ui_context") or {}
    post_login_url = auth_context.get("post_login_url") or ui_seed_url
    login_state_path = auth_context.get("state_path")
    authenticated_setup_commands: list[str] = []
    if setup_required and login_verified is True:
        if login_state_path:
            authenticated_setup_commands.append("open about:blank")
            authenticated_setup_commands.append(f'state-load "{login_state_path}"')
        if post_login_url:
            authenticated_setup_commands.append(f"goto {post_login_url}")

    context_decisions = await _analyze_ui_execution_context(state, ui_cases)
    ui_cases = _apply_ui_execution_context_plan(ui_cases, context_decisions)
    batches = _build_ui_case_batches(ui_cases, ui_seed_url, authenticated_setup_commands)
    screenshot_dir = _ensure_screenshot_dir(task_id)
    exec_result = await _execute_ui_case_batches(batches, task_id, screenshot_dir, state)

    state["ui_execution_result"] = exec_result

    artifacts = state.get("artifacts") or {}
    artifacts["ui_screenshots"] = exec_result["screenshots"]
    artifacts["ui_screenshot_evidence"] = exec_result.get("screenshot_evidence", [])
    artifacts["ui_snapshots"] = exec_result["snapshot_texts"]
    artifacts["ui_case_evidence"] = [
        {
            "case_index": case["case_index"],
            "title": case["title"],
            "status": case["status"],
            "screenshots": case["screenshots"],
            "screenshot_evidence": case.get("screenshot_evidence", []),
        }
        for case in exec_result["cases"]
    ]
    artifacts["ui_normalization_warnings"] = exec_result["normalization_warnings"]
    artifacts["ui_commands"] = [
        {
            "case_index": result["case_index"],
            "case_title": result["case_title"],
            "command": result["command"],
            "normalized_command": result.get("normalized_command"),
            "status": result.get("status", "executed"),
            "status_code": result["status_code"],
            "stdout": result.get("stdout", "")[:500],
            "stderr": result.get("stderr", "")[:500],
            "screenshot": result.get("screenshot"),
            "screenshot_evidence": result.get("screenshot_evidence"),
            "evidence_label": result.get("evidence_label"),
            "evidence_detail": result.get("evidence_detail"),
            "normalization": result.get("normalization"),
        }
        for result in exec_result["commands"]
    ]
    state["tool_summary"] = summarize_tool_calls(state.get("tool_calls"))
    artifacts["tool_calls"] = state.get("tool_calls", [])
    artifacts["tool_summary"] = state["tool_summary"]
    state["artifacts"] = artifacts

    status = "done" if exec_result["all_passed"] else "failed"
    detail = (
        f"Executed {exec_result['completed']} UI case(s): "
        f"{exec_result['passed']} passed, {exec_result['failed']} failed"
    )
    state.setdefault("workflow_steps", []).append(
        {"node": "ui_runner", "status": status, "detail": detail}
    )
    await persist_progress(state, "ui_runner", status, detail)
    return state

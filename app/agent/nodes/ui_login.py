import base64
import json
import hashlib
import logging
import re
from pathlib import Path

from langchain_core.messages import HumanMessage

from app.agent.progress import persist_progress
from app.agent.prompts import LOGIN_ASSIST_PROMPT, LOGIN_DETAILS_PROMPT, LOGIN_VERIFY_PROMPT
from app.agent.state import AgentState
from app.agent.tool_registry import install_tool_context, record_tool_call
from app.core.llm_gateway import llm_gateway
from app.tools.playwright_commands import command_name, normalize_playwright_commands

logger = logging.getLogger(__name__)

def _normalize_snapshot_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip().lower()


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


async def _run_setup_playwright_command(state: AgentState, command: str) -> dict:
    from app.tools.playwright_tool import run_playwright_cli_command

    result = await run_playwright_cli_command(command)
    status_code = result.get("status_code", -1)
    record_tool_call(
        state,
        tool_name="ui.playwright_cli",
        layer="ui",
        status="success" if status_code == 0 else "failed",
        input_summary={"command": command, "phase": "pre_test_setup"},
        output_summary={
            "status_code": status_code,
            "stdout_chars": len(result.get("stdout", "")),
            "stderr": str(result.get("stderr", ""))[:300],
        },
    )
    return result


def _fallback_verify_setup(initial_snapshot: str, post_snapshot: str) -> tuple[bool, str, dict]:
    initial_norm = _normalize_snapshot_text(initial_snapshot)
    post_norm = _normalize_snapshot_text(post_snapshot)

    if not post_norm:
        return False, "Post-setup snapshot was empty.", {
            "initial_snapshot_present": bool(initial_norm),
            "post_setup_snapshot_present": False,
            "detected_page_kind": "unknown",
        }

    changed = bool(initial_norm and post_norm and initial_norm != post_norm)
    if changed:
        return True, "Setup commands changed the page state; LLM verification was unavailable.", {
            "initial_snapshot_present": bool(initial_norm),
            "post_setup_snapshot_present": True,
            "detected_page_kind": "ready",
            "snapshot_changed": True,
        }
    return False, "Page snapshot did not change enough after setup to verify readiness.", {
        "initial_snapshot_present": bool(initial_norm),
        "post_setup_snapshot_present": True,
        "detected_page_kind": "unknown",
        "snapshot_changed": False,
    }


async def _verify_setup_result_with_llm(
    db,
    *,
    target_url: str,
    setup_instructions: str,
    initial_snapshot: str,
    post_snapshot: str,
) -> tuple[bool, str, dict]:
    if db is None:
        return _fallback_verify_setup(initial_snapshot, post_snapshot)
    try:
        llm = await llm_gateway.get_planner(db)
        prompt = LOGIN_VERIFY_PROMPT.format(
            target_url=target_url,
            login_instructions=setup_instructions,
            initial_snapshot=initial_snapshot[:4000],
            post_snapshot=post_snapshot[:5000],
        )
        resp = await llm.ainvoke([HumanMessage(content=prompt)])
        content = resp.content if hasattr(resp, "content") else str(resp)
        parsed = _parse_json_object(str(content))
        verified = bool(parsed.get("verified"))
        reason = _clean_login_value(parsed.get("reason")) or (
            "LLM verified setup result" if verified else "LLM did not verify setup result"
        )
        return verified, reason, {
            "initial_snapshot_present": bool(initial_snapshot),
            "post_setup_snapshot_present": bool(post_snapshot),
            "detected_page_kind": parsed.get("detected_page_kind", "unknown"),
            "signals": parsed.get("signals", []),
            "snapshot_changed": _normalize_snapshot_text(initial_snapshot) != _normalize_snapshot_text(post_snapshot),
        }
    except Exception as exc:
        logger.warning("Setup verification LLM call failed: %s", exc)
        return _fallback_verify_setup(initial_snapshot, post_snapshot)


def _extract_page_url(snapshot: str | None) -> str | None:
    match = re.search(r"^- Page URL:\s*(\S+)", snapshot or "", flags=re.MULTILINE)
    return match.group(1).strip() if match else None


def _clean_login_value(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip().strip("\"'`")
    if not text or text.lower() in {"null", "none", "unknown", "n/a"}:
        return None
    return text


def _state_auth_credentials(state: AgentState) -> dict[str, str]:
    raw = state.get("auth_credentials") or {}
    if not isinstance(raw, dict):
        return {}
    credentials: dict[str, str] = {}
    for key in ("username", "password", "captcha"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            credentials[key] = value.strip()
    return credentials


def _merge_runtime_credentials(
    login_details: dict,
    credentials: dict[str, str],
    captcha_value: str | None,
) -> dict:
    if not credentials and not captcha_value:
        return login_details
    merged = dict(login_details)
    provided = dict(merged.get("provided_values") or {})
    for key in ("username", "password"):
        if credentials.get(key):
            provided[key] = credentials[key]
    if captcha_value:
        provided["captcha"] = captcha_value
    elif credentials.get("captcha"):
        provided["captcha"] = credentials["captcha"]
    merged["provided_values"] = provided
    if credentials.get("username") or credentials.get("password"):
        merged["requires_browser_setup"] = True
        merged["setup_type"] = merged.get("setup_type") or "login"
    return merged


def _instructions_with_runtime_credentials(
    login_instructions: str | None,
    credentials: dict[str, str],
    captcha_value: str | None,
    captcha_mode: str,
) -> str:
    parts = [(login_instructions or "").strip()]
    credential_lines = []
    if credentials.get("username"):
        credential_lines.append(f"用户名: {credentials['username']}")
    if credentials.get("password"):
        credential_lines.append(f"密码: {credentials['password']}")
    effective_captcha = captcha_value or credentials.get("captcha")
    if effective_captcha:
        credential_lines.append(f"验证码: {effective_captcha}")
    elif captcha_mode == "dynamic":
        credential_lines.append("验证码: 使用页面图片识别结果")
    if credential_lines:
        parts.append("测试登录凭据:\n" + "\n".join(credential_lines))
    if captcha_mode == "dynamic":
        parts.append("验证码策略: 动态图片验证码，已由 Vision 模型识别后填写。")
    elif captcha_mode == "static":
        parts.append("验证码策略: 固定验证码，使用用户填写的验证码。")
    return "\n".join(part for part in parts if part).strip()


def _extract_captcha_text_from_model_response(value: str) -> str | None:
    text = str(value or "").strip()
    parsed = _parse_json_object(text)
    for key in ("captcha", "code", "text", "value"):
        candidate = _clean_login_value(parsed.get(key)) if parsed else None
        if candidate and 2 <= len(candidate) <= 12:
            return candidate
    match = re.search(r"[A-Za-z0-9]{2,12}", text)
    return match.group(0) if match else None


async def _recognize_dynamic_captcha_with_vision(
    db,
    *,
    screenshot_path: Path,
    page_snapshot: str,
) -> tuple[str | None, str]:
    if db is None:
        return None, "未提供数据库会话，无法加载默认 Vision 模型。"
    if not screenshot_path.exists():
        return None, "登录页截图不存在，无法识别动态验证码。"
    try:
        image_b64 = base64.b64encode(screenshot_path.read_bytes()).decode("ascii")
        llm = await llm_gateway.get_vision(db)
        message = HumanMessage(
            content=[
                {
                    "type": "text",
                    "text": (
                        "识别登录页面中的图片验证码。只输出 JSON，例如 "
                        '{"captcha":"A1B2"}。如果看不到验证码，输出 {"captcha": null}。\n'
                        f"页面结构摘要：{page_snapshot[:1500]}"
                    ),
                },
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                },
            ]
        )
        resp = await llm.ainvoke([message])
        content = resp.content if hasattr(resp, "content") else str(resp)
        captcha = _extract_captcha_text_from_model_response(str(content))
        if captcha:
            return captcha, "Vision 模型已识别动态验证码。"
        return None, "Vision 模型未能识别动态验证码。"
    except Exception as exc:
        logger.warning("Dynamic captcha vision recognition failed: %s", exc)
        return None, f"Vision 模型识别动态验证码失败：{str(exc)[:160]}"


_BLOCKING_PAGE_ERROR_MARKERS = (
    "500 internal server error",
    "fatal error",
    "internal server error",
    "no space left",
    "session_start",
    "stack trace",
    "traceback",
    "thinkphp",
    "uncaught exception",
    "php warning",
    "php fatal",
    "系统错误",
    "磁盘空间不足",
    "无法进行",
    "服务器错误",
)


def _looks_like_blocking_page_error(snapshot: str | None, notes: str | None = None) -> bool:
    text = _normalize_snapshot_text(f"{snapshot or ''} {notes or ''}")
    return any(marker in text for marker in _BLOCKING_PAGE_ERROR_MARKERS)


def _friendly_setup_failure_reason(exc: Exception, snapshot: str | None = None) -> str:
    text = str(exc)
    if _looks_like_blocking_page_error(snapshot, text):
        return "目标页面显示系统错误，无法执行登录或其他前置准备。"
    normalized = text.lower()
    if "auth_unavailable" in normalized or "503" in normalized or "504" in normalized:
        return "模型服务暂时不可用，无法解析并执行测试前置准备信息。"
    return f"测试前置准备失败：{text[:160]}"


def _file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


async def _attach_setup_screenshot_evidence(
    state: AgentState,
    *,
    task_id: str,
    screenshot_path: Path,
    label: str,
    detail: str,
) -> None:
    if not screenshot_path.exists():
        return

    from app.services.screenshot_storage import store_screenshot

    storage = await store_screenshot(screenshot_path, task_id)
    evidence = {
        "path": str(screenshot_path),
        "filename": screenshot_path.name,
        "case_index": "setup",
        "case_title": "测试前置准备",
        "step": 1,
        "step_index": 0,
        "label": label,
        "title": f"测试前置准备 - {label}",
        "detail": detail,
        "action": detail,
        "after_command": "open target and inspect page",
        "source_command": "pre-test setup",
        "storage": storage,
    }
    content_hash = _file_sha256(screenshot_path)
    if content_hash:
        evidence["content_hash"] = content_hash
    if storage.get("url"):
        evidence["url"] = storage["url"]

    artifacts = state.get("artifacts") or {}
    artifacts.setdefault("ui_screenshots", []).append(str(screenshot_path))
    artifacts.setdefault("ui_screenshot_evidence", []).append(evidence)
    artifacts.setdefault("ui_case_evidence", []).append(
        {
            "case_index": "setup",
            "title": "测试前置准备",
            "status": "failed",
            "screenshots": [str(screenshot_path)],
            "screenshot_evidence": [evidence],
            "skip_reason": detail,
        }
    )
    state["artifacts"] = artifacts


def _parse_login_details_response(content: str) -> dict:
    parsed = _parse_json_object(content)
    details: dict[str, str] = {}
    details["requires_browser_setup"] = bool(parsed.get("requires_browser_setup"))
    setup_type = _clean_login_value(parsed.get("setup_type")) or "none"
    details["setup_type"] = setup_type
    provided = parsed.get("provided_values")
    if isinstance(provided, dict):
        details["provided_values"] = {
            str(key): value
            for key, value in provided.items()
            if _clean_login_value(value) is not None
        }
    notes = _clean_login_value(parsed.get("notes"))
    if notes:
        details["notes"] = notes
    return details


async def _extract_login_details_with_llm(
    db,
    *,
    page_snapshot: str,
    login_instructions: str,
    target_url: str,
) -> dict[str, str]:
    if db is None:
        return {}
    try:
        llm = await llm_gateway.get_planner(db)
        prompt = LOGIN_DETAILS_PROMPT.format(
            page_snapshot=page_snapshot[:4000],
            login_instructions=login_instructions,
            target_url=target_url,
        )
        resp = await llm.ainvoke([HumanMessage(content=prompt)])
        content = resp.content if hasattr(resp, "content") else str(resp)
        return _parse_login_details_response(str(content))
    except Exception as exc:
        logger.warning("Login instruction parsing failed: %s", exc)
        return {}


async def run(state: AgentState) -> AgentState:
    """Execute optional pre-test browser setup using LLM + playwright-cli.

    The user-provided instructions may describe login, tenant selection,
    consent handling, test scope, or no browser action at all. The model
    decides whether browser setup is required.
    """
    install_tool_context(state)
    credentials = _state_auth_credentials(state)
    captcha_mode = str(state.get("captcha_mode") or "none").lower()
    login_instructions = state.get("setup_instructions") or state.get("login_instructions")
    if credentials and not login_instructions:
        login_instructions = "使用测试账号登录目标系统，登录后验证已进入受保护页面。"
    target_url = state.get("target_url", "")
    task_id = state.get("task_id", "unknown")
    db = state.get("db_session")

    # No login instructions — pass through
    if not login_instructions:
        state["login_result"] = {
            "required": False,
            "executed": False,
            "verified": None,
            "reason": "No pre-test setup instructions provided",
        }
        state["setup_result"] = state["login_result"]
        state["login_verified"] = None
        state["login_verification_reason"] = "No pre-test setup instructions provided"
        state["authenticated_ui_context"] = {
            "setup_required": False,
            "setup_executed": False,
            "detected_page_kind": "initial_or_unknown",
        }
        state.setdefault("workflow_steps", []).append(
            {"node": "ui_login", "status": "skipped", "detail": "No pre-test setup instructions provided"}
        )
        await persist_progress(state, "ui_login", "skipped", "No pre-test setup instructions")
        return state

    from app.agent.nodes.ui_runner import _ensure_screenshot_dir

    screenshot_dir = _ensure_screenshot_dir(task_id)
    login_commands = []
    login_screenshot = None
    login_snapshot = None
    initial_snapshot = ""
    recognized_captcha = None

    try:
        # Step 1: Open the target page
        open_result = await _run_setup_playwright_command(state, f"open {target_url}")
        login_commands.append(f"open {target_url}")
        if open_result.get("status_code", -1) != 0:
            state["last_error"] = f"Failed to open {target_url}: {open_result.get('stderr', '')}"
            state["login_result"] = {
                "required": True,
                "executed": False,
                "verified": False,
                "reason": f"Cannot open {target_url}",
            }
            state["setup_result"] = state["login_result"]
            state["login_verified"] = False
            state["login_verification_reason"] = f"Cannot open {target_url}"
            state["authenticated_ui_context"] = {
                "setup_required": True,
                "setup_executed": False,
                "detected_page_kind": "unreachable",
            }
            state.setdefault("workflow_steps", []).append(
                {"node": "ui_login", "status": "failed", "detail": f"Cannot open {target_url}"}
            )
            await persist_progress(state, "ui_login", "failed", "Cannot open URL")
            return state

        # Step 2: Take initial snapshot
        snap_result = await _run_setup_playwright_command(state, "snapshot")
        login_commands.append("snapshot")
        initial_snapshot = snap_result.get("stdout", "")

        # Step 3: Take initial screenshot
        initial_screenshot_path = screenshot_dir / "login_000_before.png"
        shot_cmd = f'screenshot --filename "{initial_screenshot_path}"'
        await _run_setup_playwright_command(state, shot_cmd)
        login_commands.append("screenshot")

        if captcha_mode == "dynamic":
            recognized_captcha, captcha_reason = await _recognize_dynamic_captcha_with_vision(
                db,
                screenshot_path=initial_screenshot_path,
                page_snapshot=initial_snapshot,
            )
            state["ui_captcha_result"] = {
                "mode": "dynamic",
                "recognized": bool(recognized_captcha),
                "reason": captcha_reason,
            }
            record_tool_call(
                state,
                tool_name="vision.captcha_recognize",
                layer="ui",
                status="success" if recognized_captcha else "failed",
                input_summary={"phase": "ui_login", "screenshot": str(initial_screenshot_path)},
                output_summary={"recognized": bool(recognized_captcha), "reason": captcha_reason},
            )
            if not recognized_captcha:
                state["last_error"] = captcha_reason
                state["setup_instructions"] = str(login_instructions)
                state["login_instructions"] = str(login_instructions)
                state["login_playwright_commands"] = login_commands
                state["ui_login_snapshot"] = initial_snapshot
                state["ui_login_screenshot"] = str(initial_screenshot_path)
                state["login_result"] = {
                    "required": True,
                    "executed": False,
                    "verified": False,
                    "reason": captcha_reason,
                    "setup_type": "login",
                }
                state["setup_result"] = state["login_result"]
                state["login_verified"] = False
                state["login_verification_reason"] = captcha_reason
                state["authenticated_ui_context"] = {
                    "setup_required": True,
                    "setup_executed": False,
                    "detected_page_kind": "captcha_unresolved",
                    "state_path": None,
                }
                await _attach_setup_screenshot_evidence(
                    state,
                    task_id=task_id,
                    screenshot_path=initial_screenshot_path,
                    label="动态验证码识别失败",
                    detail=captcha_reason,
                )
                state.setdefault("workflow_steps", []).append(
                    {"node": "ui_login", "status": "failed", "detail": captcha_reason}
                )
                await persist_progress(state, "ui_login", "failed", captcha_reason)
                return state

        if _looks_like_blocking_page_error(initial_snapshot):
            reason = "目标页面显示系统错误，无法执行登录或其他前置准备。"
            post_login_url = _extract_page_url(initial_snapshot)
            await _attach_setup_screenshot_evidence(
                state,
                task_id=task_id,
                screenshot_path=initial_screenshot_path,
                label="前置准备前页面异常",
                detail=reason,
            )
            state["last_error"] = reason
            state["setup_instructions"] = str(login_instructions)
            state["login_instructions"] = str(login_instructions)
            state["login_playwright_commands"] = login_commands
            state["ui_login_snapshot"] = initial_snapshot
            state["ui_login_screenshot"] = str(initial_screenshot_path)
            state["login_result"] = {
                "required": True,
                "executed": False,
                "verified": False,
                "reason": reason,
                "setup_type": "unavailable",
            }
            state["setup_result"] = state["login_result"]
            state["login_verified"] = False
            state["login_verification_reason"] = reason
            state["authenticated_ui_context"] = {
                "setup_required": True,
                "setup_executed": False,
                "detected_page_kind": "unavailable",
                "post_login_url": post_login_url,
                "state_path": None,
            }
            state.setdefault("workflow_steps", []).append(
                {"node": "ui_login", "status": "failed", "detail": reason}
            )
            await persist_progress(state, "ui_login", "failed", reason)
            return state

        login_details = await _extract_login_details_with_llm(
            db,
            page_snapshot=initial_snapshot,
            login_instructions=_instructions_with_runtime_credentials(
                str(login_instructions),
                credentials,
                recognized_captcha,
                captcha_mode,
            ),
            target_url=target_url,
        )
        login_details = _merge_runtime_credentials(login_details, credentials, recognized_captcha)

        if login_details.get("requires_browser_setup") is False:
            post_login_url = _extract_page_url(initial_snapshot)
            if _looks_like_blocking_page_error(initial_snapshot, login_details.get("notes")):
                reason = (
                    login_details.get("notes")
                    or "Target page showed a blocking system error before pre-test setup could run."
                )
                await _attach_setup_screenshot_evidence(
                    state,
                    task_id=task_id,
                    screenshot_path=screenshot_dir / "login_000_before.png",
                    label="前置准备前页面异常",
                    detail=reason,
                )
                state["last_error"] = reason
                state["setup_instructions"] = str(login_instructions)
                state["login_instructions"] = str(login_instructions)
                state["login_playwright_commands"] = login_commands
                state["ui_login_snapshot"] = initial_snapshot
                state["ui_login_screenshot"] = str(screenshot_dir / "login_000_before.png")
                state["login_result"] = {
                    "required": True,
                    "executed": False,
                    "verified": False,
                    "reason": reason,
                    "setup_type": login_details.get("setup_type", "unavailable"),
                }
                state["setup_result"] = state["login_result"]
                state["login_verified"] = False
                state["login_verification_reason"] = reason
                state["authenticated_ui_context"] = {
                    "setup_required": True,
                    "setup_executed": False,
                    "detected_page_kind": "unavailable",
                    "post_login_url": post_login_url,
                    "state_path": None,
                    "setup_details": login_details,
                }
                state.setdefault("workflow_steps", []).append(
                    {"node": "ui_login", "status": "failed", "detail": reason}
                )
                await persist_progress(state, "ui_login", "failed", reason)
                return state

            state["setup_instructions"] = str(login_instructions)
            state["login_instructions"] = str(login_instructions)
            state["login_playwright_commands"] = login_commands
            state["ui_login_snapshot"] = initial_snapshot
            state["ui_login_screenshot"] = str(screenshot_dir / "login_000_before.png")
            state["login_result"] = {
                "required": False,
                "executed": False,
                "verified": None,
                "reason": login_details.get("notes") or "Model determined no browser setup was required.",
                "setup_type": login_details.get("setup_type", "none"),
            }
            state["setup_result"] = state["login_result"]
            state["login_verified"] = None
            state["login_verification_reason"] = state["login_result"]["reason"]
            state["authenticated_ui_context"] = {
                "setup_required": False,
                "setup_executed": False,
                "detected_page_kind": "initial_or_ready",
                "post_login_url": post_login_url,
                "state_path": None,
                "setup_details": login_details,
            }
            detail = state["login_result"]["reason"]
            state.setdefault("workflow_steps", []).append(
                {"node": "ui_login", "status": "skipped", "detail": detail}
            )
            await persist_progress(state, "ui_login", "skipped", detail)
            return state

        # Step 4: Ask LLM to generate login commands
        llm = await llm_gateway.get_planner(db)
        prompt = LOGIN_ASSIST_PROMPT.format(
            page_snapshot=initial_snapshot[:4000],
            login_instructions=_instructions_with_runtime_credentials(
                str(login_instructions),
                credentials,
                recognized_captcha,
                captcha_mode,
            ),
            login_details=json.dumps(login_details, ensure_ascii=False),
            target_url=target_url,
        )
        resp = await llm.ainvoke([HumanMessage(content=prompt)])
        content = resp.content if hasattr(resp, "content") else str(resp)

        # Parse LLM response into commands
        llm_commands = []
        for line in content.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("//"):
                continue
            if line.startswith("```"):
                continue
            llm_commands.append(line)

        if not llm_commands:
            state["last_error"] = "LLM did not generate any setup commands"
            state["login_result"] = {
                "required": True,
                "executed": False,
                "verified": False,
                "reason": "LLM returned no setup commands",
            }
            state["setup_result"] = state["login_result"]
            state["login_verified"] = False
            state["login_verification_reason"] = "LLM returned no setup commands"
            state["authenticated_ui_context"] = {
                "setup_required": True,
                "setup_executed": False,
                "detected_page_kind": "setup_failed",
            }
            state.setdefault("workflow_steps", []).append(
                {"node": "ui_login", "status": "failed", "detail": "LLM returned no setup commands"}
            )
            await persist_progress(state, "ui_login", "failed", "LLM returned no setup commands")
            return state

        # Step 5: Execute LLM-generated setup commands after dialect normalization
        llm_specs = normalize_playwright_commands(llm_commands, include_unsupported=True)
        for i, spec in enumerate(llm_specs):
            if spec.get("skip"):
                login_commands.append(f"# skipped: {spec.get('source_command', '')}")
                continue

            cmd = spec["command"]
            if spec.get("kind") == "screenshot":
                shot_path = screenshot_dir / f"login_llm_{i:03d}.png"
                cmd = f'screenshot --filename "{shot_path}"'

            result = await _run_setup_playwright_command(state, cmd)
            login_commands.append(cmd)

            # Take screenshot after each fill/click
            if command_name(cmd) in {"fill", "click", "type"}:
                shot_num = len([c for c in login_commands if c.startswith(("fill", "click", "type"))])
                shot_cmd = f'screenshot --filename "{screenshot_dir / f"login_{shot_num:03d}_step.png"}"'
                await _run_setup_playwright_command(state, shot_cmd)
                login_commands.append("screenshot")

            # Stop on critical failure
            if result.get("status_code", -1) != 0:
                stderr = result.get("stderr", "")
                if "not found" in stderr.lower() or "error" in stderr.lower():
                    logger.warning("Setup command failed: %s -- %s", cmd, stderr[:200])
                    break

        # Step 6: Take post-login snapshot
        post_snap = await _run_setup_playwright_command(state, "snapshot")
        login_commands.append("snapshot")
        login_snapshot = post_snap.get("stdout", "")

        # Step 7: Take post-login screenshot
        shot_cmd = f'screenshot --filename "{screenshot_dir / "login_final.png"}"'
        await _run_setup_playwright_command(state, shot_cmd)
        login_commands.append("screenshot")
        login_screenshot = str(screenshot_dir / "login_final.png")

        login_verified, verification_reason, auth_context = await _verify_setup_result_with_llm(
            db,
            target_url=target_url,
            setup_instructions=str(login_instructions),
            initial_snapshot=initial_snapshot,
            post_snapshot=login_snapshot,
        )

        post_login_url = _extract_page_url(login_snapshot)
        state_path = None
        if login_verified:
            state_file = screenshot_dir / "login_state.json"
            state_result = await _run_setup_playwright_command(state, f'state-save "{state_file}"')
            login_commands.append("state-save")
            if state_result.get("status_code", -1) == 0 and state_file.exists():
                state_path = str(state_file)

        state["login_playwright_commands"] = login_commands
        state["setup_instructions"] = str(login_instructions)
        state["login_instructions"] = str(login_instructions)
        state["ui_login_snapshot"] = login_snapshot
        state["ui_login_screenshot"] = login_screenshot
        state["login_result"] = {
            "required": True,
            "executed": True,
            "verified": login_verified,
            "reason": verification_reason,
            "commands_executed": len(login_commands),
            "setup_type": login_details.get("setup_type", "other"),
        }
        state["setup_result"] = state["login_result"]
        state["login_verified"] = login_verified
        state["login_verification_reason"] = verification_reason
        state["authenticated_ui_context"] = {
            "setup_required": True,
            "setup_executed": True,
            **auth_context,
            "login_screenshot": login_screenshot,
            "post_login_url": post_login_url,
            "state_path": state_path,
            "setup_details": login_details,
        }
        if login_screenshot and not login_verified:
            await _attach_setup_screenshot_evidence(
                state,
                task_id=task_id,
                screenshot_path=Path(login_screenshot),
                label="前置准备后未通过验证",
                detail=verification_reason,
            )

        step_status = "done" if login_verified else "failed"
        detail = (
            f"Pre-test setup executed: {len(llm_commands)} LLM commands. {verification_reason}"
        )
        if not login_verified and not state.get("last_error"):
            state["last_error"] = f"Pre-test setup verification failed: {verification_reason}"
        state.setdefault("workflow_steps", []).append(
            {"node": "ui_login", "status": step_status, "detail": detail}
        )
        await persist_progress(state, "ui_login", step_status, detail)

    except Exception as e:
        logger.warning("Pre-test setup flow failed: %s", e)
        reason = _friendly_setup_failure_reason(e, initial_snapshot)
        screenshot_candidate = Path(login_screenshot) if login_screenshot else screenshot_dir / "login_000_before.png"
        await _attach_setup_screenshot_evidence(
            state,
            task_id=task_id,
            screenshot_path=screenshot_candidate,
            label="前置准备失败",
            detail=reason,
        )
        state["last_error"] = reason
        state["login_result"] = {
            "required": True,
            "executed": True,
            "verified": False,
            "reason": reason,
        }
        state["setup_result"] = state["login_result"]
        state["login_verified"] = False
        state["login_verification_reason"] = reason
        state["authenticated_ui_context"] = {
            "setup_required": True,
            "setup_executed": True,
            "detected_page_kind": "setup_failed",
        }
        state.setdefault("workflow_steps", []).append(
            {"node": "ui_login", "status": "failed", "detail": reason}
        )
        await persist_progress(state, "ui_login", "failed", reason)

    return state

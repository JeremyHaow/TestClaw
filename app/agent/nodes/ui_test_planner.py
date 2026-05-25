import json
import logging
import re
import asyncio
from datetime import datetime, timezone

from langchain_core.messages import HumanMessage

from app.agent.progress import persist_progress
from app.agent.prompts import UI_TEST_PLANNER_PROMPT
from app.agent.state import AgentState
from app.core.llm_gateway import llm_gateway
from app.tools.playwright_commands import command_name, normalize_playwright_commands

logger = logging.getLogger(__name__)

_LOW_VALUE_TITLES = {
    "页面可访问性与基础渲染检查",
    "页面导航链接检查",
    "控制台错误检查",
    "响应式布局检查",
}
_DEFAULT_AUTH_MIN_CASES = 8
_MAX_AUTH_CASES = 12

_CREATE_WORDS = ("add", "create", "new", "新增", "添加", "创建", "新建")
_SEARCH_WORDS = ("search", "query", "filter", "find", "搜索", "查询", "筛选", "检索")
_UPDATE_WORDS = ("edit", "update", "modify", "setting", "config", "编辑", "修改", "设置", "配置")
_DELETE_WORDS = ("delete", "remove", "clear", "删除", "移除", "清空")
_EXPORT_WORDS = ("export", "download", "导出", "下载")
_REFRESH_WORDS = ("refresh", "reload", "刷新", "同步")
_SUBMIT_WORDS = ("save", "submit", "confirm", "ok", "保存", "提交", "确定", "确认")
_CANCEL_WORDS = ("cancel", "close", "back", "return", "取消", "关闭", "返回")
_DETAIL_WORDS = ("detail", "view", "open", "详情", "查看", "打开")
_LOGIN_CASE_WORDS = (
    "login success",
    "login failed",
    "wrong password",
    "empty username",
    "empty password",
    "forgot password",
    "captcha",
    "登录成功",
    "登录失败",
    "错误密码",
    "空用户名",
    "空密码",
    "忘记密码",
    "验证码",
)

def _shell_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _compact_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _extract_quoted_text(line: str) -> str:
    match = re.search(r'"([^"]{1,80})"', line)
    if match:
        return _compact_text(match.group(1))

    before_ref = line.split("[ref=", 1)[0]
    after_role = re.sub(r"^\s*-\s*\w+\s*", "", before_ref)
    return _compact_text(after_role.strip(": -"))


def _extract_actionable_items(snapshot: str, limit: int = 24) -> list[dict]:
    """Extract likely business navigation/actions from a Playwright snapshot."""
    items: list[dict] = []
    seen: set[str] = set()

    for line in (snapshot or "").splitlines():
        if "[ref=" not in line:
            continue
        if not re.search(r"\b(link|button|menuitem|tab|treeitem|option|generic)\b", line, re.I):
            continue

        ref_match = re.search(r"\[ref=([^\]]+)\]", line)
        if not ref_match:
            continue
        ref = ref_match.group(1)
        text = _extract_quoted_text(line)
        text_norm = text.lower()
        if not text or len(text) > 40:
            continue
        if re.fullmatch(r"[\W_]+", text):
            continue

        role_match = re.search(r"^\s*-\s*(\w+)", line)
        role = role_match.group(1).lower() if role_match else "action"
        score = 0
        if role in {"link", "menuitem", "tab", "treeitem"}:
            score += 3
        if "[cursor=pointer]" in line:
            score += 1
        if score == 0 and role in {"button", "generic"}:
            score = 1

        key = text_norm
        if key in seen:
            continue
        seen.add(key)
        items.append({"ref": ref, "text": text, "role": role, "score": score})

    items.sort(key=lambda item: (-item["score"], len(item["text"]), item["text"]))
    return items[:limit]


def _extract_form_controls(snapshot: str, limit: int = 24) -> list[dict]:
    controls: list[dict] = []
    seen: set[str] = set()
    for line in (snapshot or "").splitlines():
        if "[ref=" not in line:
            continue
        if not re.search(r"\b(textbox|searchbox|combobox|checkbox|radio|spinbutton)\b", line, re.I):
            continue
        ref_match = re.search(r"\[ref=([^\]]+)\]", line)
        if not ref_match:
            continue
        role_match = re.search(r"^\s*-\s*(\w+)", line)
        role = role_match.group(1).lower() if role_match else "control"
        text = _extract_quoted_text(line) or role
        key = f"{role}:{ref_match.group(1)}"
        if key in seen:
            continue
        seen.add(key)
        controls.append({"ref": ref_match.group(1), "text": text, "role": role})
    return controls[:limit]


def _extract_data_regions(snapshot: str, limit: int = 12) -> list[dict]:
    regions: list[dict] = []
    seen: set[str] = set()
    for line in (snapshot or "").splitlines():
        if "[ref=" not in line:
            continue
        if not re.search(r"\b(table|grid|row|cell|list|listitem|treegrid)\b", line, re.I):
            continue
        ref_match = re.search(r"\[ref=([^\]]+)\]", line)
        if not ref_match:
            continue
        role_match = re.search(r"^\s*-\s*(\w+)", line)
        role = role_match.group(1).lower() if role_match else "region"
        text = _extract_quoted_text(line) or role
        key = f"{role}:{ref_match.group(1)}"
        if key in seen:
            continue
        seen.add(key)
        regions.append({"ref": ref_match.group(1), "text": text, "role": role})
    return regions[:limit]


def _contains_any(value: str, words: tuple[str, ...]) -> bool:
    value_lower = value.lower()
    return any(word.lower() in value_lower for word in words)


def _action_type(item: dict) -> str:
    text = str(item.get("text") or "")
    if _contains_any(text, _CREATE_WORDS):
        return "create"
    if _contains_any(text, _SEARCH_WORDS):
        return "search"
    if _contains_any(text, _UPDATE_WORDS):
        return "update"
    if _contains_any(text, _DELETE_WORDS):
        return "delete"
    if _contains_any(text, _EXPORT_WORDS):
        return "export"
    if _contains_any(text, _REFRESH_WORDS):
        return "refresh"
    role = str(item.get("role") or "")
    if role in {"button", "generic"}:
        return "interaction"
    return "navigation"


def _run_code_command(script: str) -> str:
    return f"run-code {_shell_quote(script)}"


def _conditional_click_labels_command(labels: tuple[str, ...], roles: tuple[str, ...] = ("button", "link")) -> str:
    labels_json = json.dumps(list(labels), ensure_ascii=False)
    roles_json = json.dumps(list(roles), ensure_ascii=False)
    script = (
        "async page => { "
        f"const labels = {labels_json}; const roles = {roles_json}; "
        "for (const label of labels) { "
        "for (const role of roles) { "
        "const locator = page.getByRole(role, { name: new RegExp(label, 'i') }).first(); "
        "if (await locator.count()) { await locator.click(); return `clicked ${role}:${label}`; } "
        "} "
        "const textLocator = page.getByText(new RegExp(label, 'i')).first(); "
        "if (await textLocator.count()) { await textLocator.click(); return `clicked text:${label}`; } "
        "} "
        "return 'no matching action'; "
        "}"
    )
    return _run_code_command(script)


def _conditional_required_submit_command() -> str:
    labels_json = json.dumps(list(_SUBMIT_WORDS), ensure_ascii=False)
    script = (
        "async page => { "
        "const required = page.locator('input[required], textarea[required], select[required], "
        "[aria-required=\"true\"]'); "
        "if (await required.count() === 0) return 'skip submit: no required empty fields'; "
        f"const labels = {labels_json}; "
        "for (const label of labels) { "
        "const button = page.getByRole('button', { name: new RegExp(label, 'i') }).first(); "
        "if (await button.count()) { await button.click(); return `submitted empty form:${label}`; } "
        "} "
        "return 'skip submit: no safe submit button'; "
        "}"
    )
    return _run_code_command(script)


def _conditional_open_first_record_command() -> str:
    script = (
        "async page => { "
        "const selectors = ["
        "'table a', '[role=\"table\"] a', '[role=\"grid\"] a', '[role=\"row\"] a', "
        "'[role=\"listitem\"] a'"
        "]; "
        "for (const selector of selectors) { "
        "const locator = page.locator(selector).first(); "
        "if (await locator.count()) { await locator.click(); return `opened record via ${selector}`; } "
        "} "
        "return 'no linked record available'; "
        "}"
    )
    return _run_code_command(script)


def _is_low_value_case(case: dict, prepared_context: bool) -> bool:
    title = str(case.get("title") or "")
    category = str(case.get("category") or "").upper()
    command_text = " ".join(str(command) for command in case.get("playwright_commands") or [])
    combined = f"{title} {category} {command_text}".lower()
    if prepared_context and (
        category == "AUTH"
        or any(word.lower() in combined for word in _LOGIN_CASE_WORDS)
    ):
        return True
    commands = [
        command_name(command)
        for command in (case.get("playwright_commands") or [])
        if isinstance(command, str)
    ]
    only_passive = bool(commands) and all(
        name in {"open", "goto", "snapshot", "screenshot"} for name in commands
    )
    if prepared_context and only_passive:
        return True
    return title in _LOW_VALUE_TITLES


def _business_case_for_action(item: dict, index: int) -> dict:
    text = item["text"]
    ref = item.get("ref")
    action_type = _action_type(item)
    click_command = f"click {ref}" if ref else f"click {_shell_quote(text)}"
    if action_type == "create":
        title = f"新增/创建流程入口检查：{text}"
        expected = [
            f"点击“{text}”后进入新增、创建或编辑表单状态",
            "页面展示可填写字段、保存/取消入口或明确的业务提示",
            "没有退回到准备前状态",
        ]
    elif action_type == "search":
        title = f"查询/筛选操作入口检查：{text}"
        expected = [
            f"“{text}”入口可触发查询或筛选相关状态",
            "页面仍保持在业务列表或结果区域",
            "没有出现阻断性错误",
        ]
    elif action_type == "update":
        title = f"编辑/设置流程入口检查：{text}"
        expected = [
            f"点击“{text}”后进入设置、编辑或配置相关状态",
            "页面展示可查看或可修改的业务内容",
            "没有出现阻断性错误",
        ]
    elif action_type == "delete":
        title = f"删除保护或批量操作校验：{text}"
        expected = [
            f"“{text}”入口不会在缺少明确确认的情况下误删数据",
            "页面展示确认、校验提示或保持原有数据状态",
            "没有出现阻断性错误",
        ]
    elif action_type == "export":
        title = f"导出/下载操作检查：{text}"
        expected = [
            f"“{text}”入口可触发导出、下载或相关提示",
            "页面没有出现阻断性错误",
        ]
    elif action_type == "refresh":
        title = f"刷新/同步操作检查：{text}"
        expected = [
            f"“{text}”入口可刷新或同步当前业务数据",
            "刷新后页面仍展示有效业务内容",
        ]
    else:
        title = f"业务入口可访问性检查：{text}"
        expected = [
            f"“{text}”入口可点击",
            "页面没有退回到准备前状态",
            "业务页面能够加载出导航、表格、表单或操作按钮等有效内容",
        ]

    commands = ["snapshot", click_command, "snapshot", "screenshot", "snapshot", "screenshot"]
    if action_type == "delete":
        commands = [
            "snapshot",
            click_command,
            "snapshot",
            "screenshot",
            "dialog-dismiss",
            "snapshot",
            "screenshot",
        ]

    return {
        "title": title,
        "category": {
            "create": "FORM",
            "search": "FORM",
            "update": "INTERACTION",
            "delete": "INTERACTION",
            "export": "INTERACTION",
            "refresh": "DATA_DISPLAY",
        }.get(action_type, "BUSINESS_NAVIGATION"),
        "priority": "P0" if index < 6 else "P1",
        "case_type": "ui",
        "requires_authenticated_context": True,
        "source": "authenticated_snapshot",
        "target_action": item,
        "operation_type": action_type,
        "steps": [
            f"在当前已准备好的页面上下文中定位“{text}”入口",
            f"点击“{text}”执行对应业务动作",
            "采集页面快照并检查是否发生页面/内容变化",
            "保存操作后页面截图作为证据",
            "记录该业务模块状态，下一条用例重新从准备后的入口进入",
        ],
        "expected": expected,
        "playwright_commands": commands,
    }


def _control_case(control: dict, actions: list[dict], index: int) -> dict:
    ref = control["ref"]
    text = control.get("text") or control.get("role") or "输入控件"
    role = control.get("role")
    search_action = next((item for item in actions if _action_type(item) == "search"), None)
    commands = ["snapshot"]
    category = "FORM"
    operation_type = "input"
    if role in {"textbox", "searchbox", "spinbutton"}:
        value = "TestClaw-Auto"
        commands.append(f"fill {ref} {_shell_quote(value)}")
        if search_action and search_action.get("ref"):
            commands.append(f"click {search_action['ref']}")
            operation_type = "search"
        commands.extend(["snapshot", "screenshot", "snapshot", "screenshot"])
    elif role in {"checkbox", "radio"}:
        commands.extend([f"check {ref}", "snapshot", "screenshot", f"uncheck {ref}", "snapshot", "screenshot"])
        operation_type = "selection"
    elif role == "combobox":
        commands.extend([f"click {ref}", "snapshot", "screenshot"])
        operation_type = "select"
    else:
        commands.extend(["snapshot", "screenshot"])

    return {
        "title": f"表单控件业务交互检查：{text}",
        "category": category,
        "priority": "P1" if index >= 6 else "P0",
        "case_type": "ui",
        "requires_authenticated_context": True,
        "source": "authenticated_snapshot",
        "target_action": control,
        "operation_type": operation_type,
        "steps": [
            f"定位“{text}”控件",
            "输入、选择或触发该控件",
            "如存在查询入口，则执行一次查询动作",
            "采集操作后的页面快照和截图",
        ],
        "expected": [
            "控件可交互",
            "页面状态随输入、选择或查询动作发生合理变化",
            "没有出现阻断性错误",
        ],
        "playwright_commands": commands,
    }


def _search_flow_case(controls: list[dict], actions: list[dict], index: int) -> dict | None:
    control = next((item for item in controls if item.get("role") == "searchbox"), None)
    if control is None:
        control = next(
            (item for item in controls if item.get("role") in {"textbox", "combobox"}),
            None,
        )
    if control is None:
        return None

    search_action = next((item for item in actions if _action_type(item) == "search"), None)
    commands = ["snapshot"]
    if control.get("role") in {"textbox", "searchbox"}:
        commands.append(f"fill {control['ref']} {_shell_quote('TestClaw-Auto')}")
    else:
        commands.append(f"click {control['ref']}")
    if search_action and search_action.get("ref"):
        commands.append(f"click {search_action['ref']}")
    commands.extend(["snapshot", "screenshot"])
    if control.get("role") in {"textbox", "searchbox"}:
        commands.append(f"fill {control['ref']} {_shell_quote('')}")
        if search_action and search_action.get("ref"):
            commands.append(f"click {search_action['ref']}")
        commands.extend(["snapshot", "screenshot"])

    return {
        "title": "查询/筛选业务链路检查",
        "category": "FORM",
        "priority": "P0" if index < 6 else "P1",
        "case_type": "ui",
        "requires_authenticated_context": True,
        "source": "authenticated_snapshot",
        "target_action": control,
        "operation_type": "search_flow",
        "steps": [
            "定位当前业务页面的查询、筛选或可输入条件控件",
            "输入临时查询条件并触发查询入口",
            "采集结果区域快照和截图",
            "清空查询条件并验证页面可以回到可用状态",
        ],
        "expected": [
            "查询或筛选控件可交互",
            "查询后业务列表、表格或结果区域保持可用",
            "清空条件后页面没有阻断性错误",
        ],
        "playwright_commands": commands,
    }


def _safe_form_validation_flow_case(actions: list[dict], index: int) -> dict | None:
    entry = next((item for item in actions if _action_type(item) == "create"), None)
    if entry is None:
        entry = next((item for item in actions if _action_type(item) == "update"), None)
    if entry is None:
        return None

    text = str(entry.get("text") or "表单入口")
    click_command = f"click {entry['ref']}" if entry.get("ref") else f"click {_shell_quote(text)}"
    return {
        "title": f"表单打开、必填校验与取消路径检查：{text}",
        "category": "FORM",
        "priority": "P0" if index < 6 else "P1",
        "case_type": "ui",
        "requires_authenticated_context": True,
        "source": "authenticated_snapshot",
        "target_action": entry,
        "operation_type": "safe_form_validation_flow",
        "steps": [
            f"从当前业务上下文打开“{text}”对应的新增、编辑或配置表单",
            "采集表单字段、默认值和操作按钮截图",
            "仅在页面存在 HTML 必填字段时尝试空提交，验证前端/后端必填校验",
            "通过取消、关闭或返回路径退出表单，避免产生真实写入",
        ],
        "expected": [
            "表单或配置页面能够打开",
            "必填字段为空提交时展示校验反馈，或安全跳过提交动作",
            "取消/关闭后页面回到业务上下文且没有误创建、误修改数据",
        ],
        "playwright_commands": [
            "snapshot",
            click_command,
            "snapshot",
            "screenshot",
            _conditional_required_submit_command(),
            "snapshot",
            "screenshot",
            _conditional_click_labels_command(_CANCEL_WORDS),
            "snapshot",
            "screenshot",
        ],
    }


def _record_drilldown_flow_case(data_regions: list[dict], actions: list[dict], index: int) -> dict | None:
    if not data_regions:
        return None
    detail_action = next(
        (
            item
            for item in actions
            if _contains_any(str(item.get("text") or ""), _DETAIL_WORDS)
        ),
        None,
    )
    commands = ["snapshot"]
    if detail_action and detail_action.get("ref"):
        commands.append(f"click {detail_action['ref']}")
    else:
        commands.append(_conditional_open_first_record_command())
    commands.extend(
        [
            "snapshot",
            "screenshot",
            _conditional_click_labels_command((*_CANCEL_WORDS, "list", "列表")),
            "snapshot",
            "screenshot",
        ]
    )

    return {
        "title": "列表/表格记录详情链路检查",
        "category": "DATA_DISPLAY",
        "priority": "P0" if index < 6 else "P1",
        "case_type": "ui",
        "requires_authenticated_context": True,
        "source": "authenticated_snapshot",
        "target_action": data_regions[0],
        "operation_type": "record_drilldown_flow",
        "steps": [
            "识别当前页面的数据列表、表格、网格或记录区域",
            "优先打开可见详情/查看入口；没有明确入口时尝试打开第一条安全链接记录",
            "采集详情或记录页面证据",
            "返回列表并验证上下文可恢复",
        ],
        "expected": [
            "数据区域可见且存在可探索的记录入口，或安全跳过无入口场景",
            "详情/记录页面打开后没有阻断性错误",
            "返回后列表上下文保持可用",
        ],
        "playwright_commands": commands,
    }


def _build_deep_business_flow_cases(
    actions: list[dict],
    controls: list[dict],
    data_regions: list[dict],
    start_index: int,
) -> list[dict]:
    candidates = [
        _search_flow_case(controls, actions, start_index),
        _record_drilldown_flow_case(data_regions, actions, start_index + 1),
        _safe_form_validation_flow_case(actions, start_index + 2),
    ]
    return [case for case in candidates if case]


def _build_authenticated_business_cases(
    snapshot: str,
    minimum_cases: int = _DEFAULT_AUTH_MIN_CASES,
    max_cases: int = _MAX_AUTH_CASES,
) -> list[dict]:
    actions = _extract_actionable_items(snapshot, limit=max_cases)
    controls = _extract_form_controls(snapshot, limit=16)
    data_regions = _extract_data_regions(snapshot, limit=8)
    cases = [
        {
            "title": "前置准备后页面状态与核心入口检查",
            "category": "AUTHENTICATED_HOME",
            "priority": "P0",
            "case_type": "ui",
            "requires_authenticated_context": True,
            "source": "authenticated_snapshot",
            "steps": [
                "确认当前页面已经处于前置准备后的可测试上下文",
                "采集当前页面快照",
                "检查是否存在导航、业务入口或数据区域",
                "刷新页面验证准备后的状态保持",
            ],
            "expected": [
                "当前页面不是准备前的阻塞页面",
                "导航和业务入口可见",
                "刷新后仍保持可测试状态",
            ],
            "playwright_commands": [
                "snapshot",
                "screenshot",
                "reload",
                "snapshot",
                "screenshot",
            ],
        }
    ]

    seen_titles = {cases[0]["title"]}
    for case in _build_deep_business_flow_cases(actions, controls, data_regions, len(cases)):
        if len(cases) >= max_cases:
            break
        if case["title"] in seen_titles:
            continue
        seen_titles.add(case["title"])
        cases.append(case)

    for item in actions:
        if len(cases) >= max_cases:
            break
        case = _business_case_for_action(item, len(cases))
        if case["title"] in seen_titles:
            continue
        seen_titles.add(case["title"])
        cases.append(case)

    for control in controls:
        if len(cases) >= max(max_cases, minimum_cases):
            break
        case = _control_case(control, actions, len(cases))
        if case["title"] in seen_titles:
            continue
        seen_titles.add(case["title"])
        cases.append(case)

    if len(cases) > max_cases:
        return cases[:max_cases]

    return cases


def _merge_or_replace_low_value_cases(
    llm_cases: list[dict],
    business_cases: list[dict],
    prepared_context: bool,
) -> list[dict]:
    if not business_cases:
        return llm_cases

    useful_llm_cases = [
        case for case in llm_cases if not _is_low_value_case(case, prepared_context)
    ]
    if prepared_context:
        useful_llm_cases = [
            case
            for case in useful_llm_cases
            if case.get("requires_authenticated_context") is True
            or case.get("source") == "authenticated_snapshot"
        ]
    merged = business_cases[:]
    seen_titles = {case.get("title") for case in merged}

    for case in useful_llm_cases:
        if len(merged) >= _MAX_AUTH_CASES:
            break
        title = case.get("title")
        if title in seen_titles:
            continue
        if prepared_context:
            case.setdefault("requires_authenticated_context", True)
        merged.append(case)

    return merged


def _generate_reproducible_script(
    login_commands: list[str],
    ui_cases: list[dict],
    target_url: str,
) -> str:
    """Generate a clean, commented playwright-cli script."""
    lines = []
    lines.append("# TestClaw Generated UI Test Script")
    lines.append(f"# Target: {target_url}")
    lines.append(f"# Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append("")

    if login_commands:
        lines.append("# === LOGIN SEQUENCE ===")
        for spec in normalize_playwright_commands(login_commands):
            lines.append(spec["command"])
        lines.append("")

    for i, case in enumerate(ui_cases):
        title = case.get("title", f"Test Case {i+1}")
        lines.append(f"# === {i+1}. {title} ===")
        cmds = case.get("playwright_commands", [])
        if login_commands and case.get("requires_authenticated_context"):
            actual_cmds = cmds
        elif login_commands:
            actual_cmds = cmds[:]
            while actual_cmds and command_name(actual_cmds[0]) in {"open", "goto"}:
                actual_cmds = actual_cmds[1:]
        else:
            actual_cmds = cmds

        if not actual_cmds:
            actual_cmds = ["snapshot", "screenshot"]

        for spec in normalize_playwright_commands(actual_cmds):
            lines.append(spec["command"])
        lines.append("")

    return "\n".join(lines)


def _normalize_case(case: dict) -> dict:
    """Ensure steps and expected are string arrays, and keep command repair auditable."""
    for key in ("steps", "expected"):
        val = case.get(key)
        if isinstance(val, str):
            case[key] = [s.strip() for s in val.split("\n") if s.strip()]
        elif not isinstance(val, list):
            case[key] = []

    cmds = case.get("playwright_commands", [])
    if cmds:
        case["raw_playwright_commands"] = [c for c in cmds if isinstance(c, str) and c.strip()]
        normalized = normalize_playwright_commands(case["raw_playwright_commands"])
        case["playwright_commands"] = [spec["command"] for spec in normalized if spec.get("command")]
        case["command_normalization_warnings"] = [
            {
                "source_command": spec.get("source_command"),
                "detail": spec.get("normalization"),
            }
            for spec in normalize_playwright_commands(case["raw_playwright_commands"], include_unsupported=True)
            if spec.get("normalization")
        ]
    return case


async def _planner_playwright_command(command: str) -> dict:
    from app.tools.playwright_tool import run_playwright_cli_command

    try:
        return await asyncio.wait_for(run_playwright_cli_command(command), timeout=12)
    except asyncio.TimeoutError:
        return {"status_code": -1, "stdout": "", "stderr": "Planner exploration command timeout"}


async def _explore_after_login(target_url: str, task_id: str, reset_url: str | None = None) -> tuple[list[str], str]:
    """After login, explore the dashboard: click menu items, take snapshots."""
    from app.agent.nodes.ui_runner import _ensure_screenshot_dir

    screenshot_dir = _ensure_screenshot_dir(task_id)
    explored_snapshots = []
    extra_commands = []

    # Take snapshot of current page (post-login dashboard)
    snap = await _planner_playwright_command("snapshot")
    page_text = snap.get("stdout", "")
    explored_snapshots.append(page_text)

    clickable_refs = [
        (item["ref"], item["text"])
        for item in _extract_actionable_items(page_text, limit=8)
        if item.get("score", 0) > 0
    ]

    # Click a small number of actions to enrich context; every command is bounded.
    explored_count = 0
    for ref, text in clickable_refs[:4]:
        if explored_count >= 2:
            break

        click_result = await _planner_playwright_command(f"click {ref}")
        extra_commands.append(f"click {ref}")
        if click_result.get("status_code", -1) == 0:
            snap2 = await _planner_playwright_command("snapshot")
            extra_commands.append("snapshot")
            page2 = snap2.get("stdout", "")
            if page2 and page2 != page_text:
                explored_snapshots.append(f"=== Page after clicking '{text}' ===\n{page2[:3000]}")
                safe_text = re.sub(r"[^a-zA-Z0-9_-]+", "_", text[:20]).strip("_") or "page"
                await _planner_playwright_command(
                    f'screenshot --filename "{screenshot_dir / f"explore_{explored_count:03d}_{safe_text}.png"}"'
                )
                extra_commands.append("screenshot")
                explored_count += 1
            if reset_url:
                reset_result = await _planner_playwright_command(f"goto {reset_url}")
                extra_commands.append(f"goto {reset_url}")
                if reset_result.get("status_code", -1) != 0:
                    break
        else:
            break

    combined_snapshot = "\n\n".join(explored_snapshots)
    return extra_commands, combined_snapshot


async def run(state: AgentState) -> AgentState:
    """Generate comprehensive UI test cases using LLM + page snapshot."""
    target_url = state.get("ui_seed_url") or state.get("target_url", "")
    existing_ui_cases = list(state.get("ui_cases") or [])
    preserve_existing_ui_cases = str(state.get("source_input") or "").strip().lower() == "suite"
    setup_instructions = state.get("setup_instructions") or state.get("login_instructions")
    setup_required = bool((setup_instructions or "").strip())
    login_verified = state.get("login_verified")
    setup_result = state.get("setup_result") or state.get("login_result") or {}
    login_snapshot = state.get("ui_login_snapshot")
    login_commands = state.get("login_playwright_commands") or []
    task_id = state.get("task_id", "unknown")
    db = state.get("db_session")
    replan_feedback = (state.get("agent_replan_feedback") or "").strip()
    previous_ui_result = state.get("ui_execution_result") or {}
    previous_snapshots = [
        snapshot
        for snapshot in (previous_ui_result.get("snapshot_texts") or [])[-3:]
        if isinstance(snapshot, str) and snapshot.strip()
    ]

    if setup_required and setup_result.get("required") and login_verified is False:
        detail = state.get("login_verification_reason") or "Pre-test setup verification failed; skipping UI planning"
        state.setdefault("workflow_steps", []).append(
            {"node": "ui_test_planner", "status": "failed", "detail": detail}
        )
        await persist_progress(state, "ui_test_planner", "failed", detail)
        return state

    # If no login was done, do an initial page open + snapshot
    if not login_snapshot:
        from app.tools.playwright_tool import run_playwright_cli_command
        from app.agent.nodes.ui_runner import _ensure_screenshot_dir
        screenshot_dir = _ensure_screenshot_dir(task_id)

        await run_playwright_cli_command(f"open {target_url}")
        snap_result = await run_playwright_cli_command("snapshot")
        login_snapshot = snap_result.get("stdout", "")
        await run_playwright_cli_command(f'screenshot --filename "{screenshot_dir / "initial.png"}"')
        login_commands = [f"open {target_url}", "snapshot", "screenshot"]

    # Explore the authenticated page to discover more features
    explore_commands = []
    explored_snapshot = login_snapshot
    business_cases: list[dict] = []
    if login_snapshot and state.get("login_playwright_commands"):
        try:
            post_setup_url = (state.get("authenticated_ui_context") or {}).get("post_login_url")
            explore_commands, explored_snapshot = await _explore_after_login(
                target_url,
                task_id,
                reset_url=post_setup_url,
            )
            business_cases = _build_authenticated_business_cases(explored_snapshot)
            logger.info("Exploration discovered %d extra commands", len(explore_commands))
        except Exception as e:
            logger.warning("Exploration failed: %s", e)

    if previous_snapshots:
        explored_snapshot = (
            f"{explored_snapshot}\n\n"
            "=== Previous execution snapshot evidence for replanning ===\n"
            f"{chr(10).join(previous_snapshots)[:6000]}"
        )

    ui_cases = (
        []
        if setup_required and login_verified is True and not preserve_existing_ui_cases
        else existing_ui_cases
    )

    if not ui_cases and db and explored_snapshot:
        try:
            llm = await llm_gateway.get_planner(db)
            login_info = "Current page is the active UI context after optional pre-test setup." if state.get("ui_login_snapshot") else "No pre-test setup was required"
            if setup_instructions:
                login_info += f". User-provided setup/context information: {setup_instructions[:300]}"
            if replan_feedback:
                login_info += f". Evidence evaluator feedback for this replan: {replan_feedback[:600]}"

            prompt = UI_TEST_PLANNER_PROMPT.format(
                target_url=target_url,
                page_snapshot=explored_snapshot[:8000],
                login_info=login_info,
            )
            resp = await llm.ainvoke([HumanMessage(content=prompt)])
            content = resp.content if hasattr(resp, "content") else str(resp)

            text = content.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            parsed = json.loads(text)

            if isinstance(parsed, list):
                ui_cases = parsed
            elif isinstance(parsed, dict):
                ui_cases = parsed.get("test_cases", parsed.get("cases", []))

            # Normalize all cases
            ui_cases = [_normalize_case(c) for c in ui_cases]

            logger.info("LLM generated %d UI test cases", len(ui_cases))
        except Exception as e:
            logger.warning("UI test planner LLM call failed: %s", e)

    # Fallback: generate basic cases if LLM failed
    if not ui_cases:
        from app.agent.nodes.tc_generator import _build_fallback_ui_cases
        ui_cases = _build_fallback_ui_cases(target_url, [], None)
        ui_cases = [_normalize_case(c) for c in ui_cases]

    if not preserve_existing_ui_cases and setup_required and login_verified is True:
        if not business_cases:
            business_cases = _build_authenticated_business_cases(explored_snapshot)
        ui_cases = _merge_or_replace_low_value_cases(ui_cases, business_cases, prepared_context=True)
        discovered_actions = [case.get("target_action") for case in business_cases if case.get("target_action")]
        auth_context = state.get("authenticated_ui_context") or {}
        auth_context["discovered_ui_actions"] = discovered_actions
        auth_context["business_case_count"] = len(business_cases)
        state["authenticated_ui_context"] = auth_context

    # Merge explore commands into login commands for script generation
    all_login_commands = login_commands + explore_commands

    # Generate reproducible script
    script = _generate_reproducible_script(all_login_commands, ui_cases, target_url)

    state["ui_cases"] = ui_cases
    state["ui_reproducible_script"] = script

    detail = f"Planned {len(ui_cases)} UI test cases with script"
    state.setdefault("workflow_steps", []).append(
        {"node": "ui_test_planner", "status": "done", "detail": detail}
    )
    await persist_progress(state, "ui_test_planner", "done", detail)
    return state

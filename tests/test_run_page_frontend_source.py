from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN_PAGE = ROOT / "frontend/src/pages/RunPage.vue"
RUN_COMPONENT_DIR = ROOT / "frontend/src/components/run"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _run_sources() -> str:
    sources = [_source(RUN_PAGE)]
    sources.extend(_source(path) for path in sorted(RUN_COMPONENT_DIR.glob("*.vue")))
    return "\n".join(sources)


def test_run_page_uses_mission_control_components() -> None:
    source = _source(RUN_PAGE)

    for component in [
        "RunMissionCard",
        "RunModeSelector",
        "RunPolicySelector",
        "RunAuthPreflightCard",
        "RunPreflightStatusCard",
        "RunHandoffPreview",
    ]:
        assert f"import {component}" in source
        assert f"<{component}" in source


def test_run_page_labels_remain_visible_after_extraction() -> None:
    source = _run_sources()

    for label in [
        "任务控制台",
        "测试智能体工作台",
        "任务委派",
        "测试模式",
        "API 执行策略",
        "目标上下文",
        "安全边界",
        "鉴权预检",
        "预检状态",
        "智能体执行流",
        "任务交接预览",
        "就绪",
        "阻塞",
        "需确认",
    ]:
        assert label in source

    assert "目标记忆" in source
    assert "Testing Agent Workspace" not in source
    assert "Agent Memory" not in source


def test_run_page_api_hooks_and_behaviors_remain_in_page() -> None:
    source = _source(RUN_PAGE)

    for hook in [
        "async function runPreflight",
        "async function submit",
        "api.post('/runs/preflight', buildRunPayload())",
        "api.post('/runs', buildRunPayload({ forCreate: true }))",
        "sourceReady",
        "canRun",
        "applyRoutePrefill",
        "handleDocumentSelection",
        "buildAuthConfig",
        "auth_preflight_id",
    ]:
        assert hook in source


def test_run_page_keeps_public_ui_safety_text_out_of_login_setup_on_create() -> None:
    source = _source(RUN_PAGE)

    assert "function shouldTreatSetupAsObjectiveContext(forCreate: boolean)" in source
    assert "forCreate && form.test_type === 'ui' && form.auth_mode === 'none_confirmed'" in source
    assert "const safetyContext = `安全边界：${setupInstructions}`" in source
    assert "payload.setup_instructions = setupInstructions" in source
    assert "buildRunPayload({ forCreate: true })" in source


def test_run_selectors_use_model_update_contract() -> None:
    mode_source = _source(RUN_COMPONENT_DIR / "RunModeSelector.vue")
    policy_source = _source(RUN_COMPONENT_DIR / "RunPolicySelector.vue")

    assert "modelValue" in mode_source
    assert "update:modelValue" in mode_source
    assert "测试模式" in mode_source

    assert "modelValue" in policy_source
    assert "update:modelValue" in policy_source
    assert "API 执行策略" in policy_source

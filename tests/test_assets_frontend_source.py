from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS_PAGE = ROOT / "frontend/src/pages/DocumentsPage.vue"
ENVIRONMENTS_PAGE = ROOT / "frontend/src/pages/EnvironmentsPage.vue"
TEST_CASES_PAGE = ROOT / "frontend/src/pages/TestCasesPage.vue"
AGENT_PLAN_PAGE = ROOT / "frontend/src/pages/AgentPlanPage.vue"
ASSET_COMPONENT_DIR = ROOT / "frontend/src/components/assets"
ASSET_HANDOFF = ROOT / "frontend/src/lib/assetHandoff.ts"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_phase_6_3_asset_cards_are_shared_across_pages() -> None:
    documents = _source(DOCUMENTS_PAGE)
    environments = _source(ENVIRONMENTS_PAGE)
    cases = _source(TEST_CASES_PAGE)

    assert "DocumentAssetCard" in documents
    assert "EnvironmentAssetCard" in environments
    assert "TestCaseAssetCard" in cases

    for component_name, marker in [
        ("DocumentAssetCard.vue", 'data-testid="document-asset-card"'),
        ("EnvironmentAssetCard.vue", 'data-testid="environment-asset-card"'),
        ("TestCaseAssetCard.vue", 'data-testid="test-case-asset-card"'),
    ]:
        source = _source(ASSET_COMPONENT_DIR / component_name)
        assert marker in source
        assert "rounded-lg border" in source
        assert "shadow" in source
        assert "用于新计划" in source or "加入计划" in source


def test_phase_6_3_assets_reuse_run_and_agent_plan_paths() -> None:
    combined = "\n".join(_source(path) for path in [DOCUMENTS_PAGE, ENVIRONMENTS_PAGE, TEST_CASES_PAGE])

    for hook in [
        "path: '/run'",
        "document_id: item.id",
        "base_url: item.base_url",
        "runSelectedSuite",
        "api.post('/test-cases/suites'",
        "path: '/agent-plan'",
        "from: 'asset'",
        "asset_type: 'document'",
        "asset_type: 'environment'",
        "asset_type: 'test-case'",
        "redactSensitiveText(context)",
    ]:
        assert hook in combined

    environments = _source(ENVIRONMENTS_PAGE)
    assert "test_type: 'ui'" in environments
    assert "source: item.base_url" in environments


def test_phase_6_3_loading_empty_and_card_copy_are_complete() -> None:
    combined = "\n".join(
        _source(path)
        for path in [
            DOCUMENTS_PAGE,
            ENVIRONMENTS_PAGE,
            TEST_CASES_PAGE,
            ASSET_COMPONENT_DIR / "DocumentAssetCard.vue",
            ASSET_COMPONENT_DIR / "EnvironmentAssetCard.vue",
            ASSET_COMPONENT_DIR / "TestCaseAssetCard.vue",
        ]
    )

    for label in [
        "接口文档",
        "导入 OpenAPI / Postman",
        "测试环境",
        "用例资产",
        "暂无文档",
        "暂无环境配置",
        "暂无用例",
        "加载文档中",
        "加载中",
        "Ready",
        "可运行",
        "最近结果",
    ]:
        assert label in combined

    assert "LoadingSpinner" in combined
    assert "EmptyState" in combined
    assert "<table" not in _source(TEST_CASES_PAGE).lower()


def test_test_case_assets_keep_user_visible_copy_localized() -> None:
    page = _source(TEST_CASES_PAGE)
    card = _source(ASSET_COMPONENT_DIR / "TestCaseAssetCard.vue")

    for label in [
        "已选择",
        "未选择用例",
        "选中用例套件",
        "套件",
        "功能",
        "界面",
        "接口",
        "性能",
        "安全",
    ]:
        assert label in page

    for label in ["caseTypeLabel", "接口", "界面", "用例", "手动维护", "运行 "]:
        assert label in card

    assert "{{ selectedIds.size }} selected" not in page
    assert "Selected suite" not in page
    assert ">{{ caseType }}</span>" not in card


def test_phase_6_3_handoff_redacts_asset_context_and_agent_plan_consumes_it() -> None:
    handoff = _source(ASSET_HANDOFF)
    agent_plan = _source(AGENT_PLAN_PAGE)
    environments = _source(ENVIRONMENTS_PAGE)

    for hook in [
        "REDACTED_VALUE",
        "isSensitiveQueryKey",
        "SENSITIVE_KEY_PATTERN",
        "\\b(fill|type)",
        "redactSensitiveText",
        "password|passwd|pwd|token|secret|api[_-]?key|authorization|auth|cookie|session|captcha|mfa|otp|csrf|xsrf|jwt",
    ]:
        assert hook in handoff

    for hook in [
        "importedAssetPlanContent",
        "clearImportedAssetQuery",
        "queryText(route.query.from) !== 'asset'",
        "queryText(route.query.asset_type)",
        "await clearImportedAssetQuery()",
        "await submitPlannerContent(importedContent, importedContent)",
    ]:
        assert hook in agent_plan

    assert "变量值不进入计划上下文" in environments
    assert "environmentVariableKeys" in environments
    assert "变量键：" in environments
    assert "环境管理" not in environments
    environment_plan_context = environments.split("function useEnvironmentForPlan", 1)[1].split("router.push", 1)[0]
    assert "environmentVariableKeys(item)" in environment_plan_context
    assert "item.variables" not in environment_plan_context
    assert "import { redactSensitiveText } from '../lib/assetHandoff'" in agent_plan

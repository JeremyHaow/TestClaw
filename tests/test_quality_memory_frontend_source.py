from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
QUALITY_MEMORY_PAGE = ROOT / "frontend/src/pages/QualityMemoryPage.vue"
AGENT_PLAN_PAGE = ROOT / "frontend/src/pages/AgentPlanPage.vue"


def _source() -> str:
    return QUALITY_MEMORY_PAGE.read_text(encoding="utf-8")


def test_quality_memory_page_phase_6_2_labels_are_visible() -> None:
    source = _source()

    for label in [
        "质量记忆",
        "目标记忆列表",
        "高频主题",
        "可复用资产",
        "一键用于新计划",
        "已记忆目标",
        "复用用例",
        "高频阻塞",
        "平均复用率",
        "查看记忆",
        "用于新计划",
        "运行结果分布",
        "证据复用覆盖",
        "风险优先级",
        "已知阻塞点",
        "推荐下次策略",
    ]:
        assert label in source


def test_quality_memory_page_preserves_insights_contract() -> None:
    source = _source()

    for hook in [
        "api.get<RunHistoryInsights>('/runs/insights'",
        "params: { days: 30, limit: 100 }",
        "affected_targets",
        "affected_surfaces",
        "recurring_themes",
        "evidence_reproduction",
        "recommended_next_actions",
        "quality_trend",
        "renderTrendChart",
        "statusBreakdownItems",
        "evidenceCoverageItems",
        "topRiskThemes",
    ]:
        assert hook in source


def test_quality_memory_page_routes_redacted_context_to_agent_plan() -> None:
    source = _source()

    for hook in [
        "useRouter",
        "router.push",
        "path: '/agent-plan'",
        "from: 'quality-memory'",
        "context: redactSensitiveText(context)",
        "function redactSensitiveText",
        "REDACTED_VALUE",
        "password|passwd|pwd|token|secret|api[_-]?key|authorization|cookie|session|captcha|mfa|otp|csrf|xsrf|jwt",
        "默认只读",
    ]:
        assert hook in source

    agent_plan_source = AGENT_PLAN_PAGE.read_text(encoding="utf-8")
    for hook in [
        "importedQualityMemoryPlanContent",
        "queryText(route.query.from) !== 'quality-memory'",
        "await submitPlannerContent(importedContent, importedContent)",
        "clearImportedQualityMemoryQuery",
    ]:
        assert hook in agent_plan_source


def test_quality_memory_page_renders_memory_theme_and_asset_cards_without_raw_json() -> None:
    source = _source()

    for hook in [
        'data-testid="quality-memory-target-card"',
        'data-testid="quality-memory-theme"',
        'data-testid="quality-memory-asset"',
        "selectedMemory",
        "Memory Detail",
        "buildMemoryPlanContext",
        "useMemoryForNewPlan",
        "useThemeForNewPlan",
        "useAssetForNewPlan",
    ]:
        assert hook in source

    assert "<pre" not in source.lower()
    assert "JSON.stringify" not in source

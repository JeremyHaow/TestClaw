from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROVIDERS_PAGE = ROOT / "frontend/src/pages/ProvidersPage.vue"
KNOWLEDGE_PAGE = ROOT / "frontend/src/pages/KnowledgePage.vue"
SETTINGS_PAGE = ROOT / "frontend/src/pages/SettingsPage.vue"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_phase_6_9_providers_page_renders_model_role_and_strategy_cards() -> None:
    source = _source(PROVIDERS_PAGE)

    for hook in [
        'data-testid="provider-role-card"',
        'data-testid="agent-strategy-card"',
        "Planner Model",
        "Executor(Coder) Model",
        "Vision Model",
        "用于生成测试计划和用例",
        "用于生成可执行脚本、断言和修复建议",
        "用于读取截图、页面状态和视觉证据",
        "保守模式",
        "平衡模式",
        "探索模式",
        "默认只读",
        "不执行删除/修改",
        "自动重试",
        "更主动发现路径",
        "api_execution_policy",
    ]:
        assert hook in source

    for real_api_hook in [
        "`/providers/${id}/set-default`",
        "`/providers/${id}/test`",
        "setDefault(role.model.id, role.role)",
        "testProvider(role.model.id)",
        "startEdit(role.model)",
    ]:
        assert real_api_hook in source

    assert "不伪造未持久化的全局开关" in source
    assert "api_key_masked" in source


def test_phase_6_10_knowledge_page_renders_rag_cards_with_required_metadata() -> None:
    source = _source(KNOWLEDGE_PAGE)

    for hook in [
        'data-testid="knowledge-card"',
        "RAG 知识库",
        "类型",
        "片段",
        "最近更新",
        "使用次数",
        "查看",
        "重新索引",
        "禁用",
        "knowledgeType(entry)",
        "fragmentCount(entry)",
        "usageCount(entry)",
        "lastUpdatedLabel(entry)",
        "embedding_available",
    ]:
        assert hook in source

    assert "usage_count" in source
    assert "chunk_count" in source
    assert "fragment_count" in source


def test_phase_6_10_knowledge_actions_use_only_existing_api_capabilities() -> None:
    source = _source(KNOWLEDGE_PAGE)

    assert "function reindexEntry" in source
    assert "api.put(`/knowledge/${entry.id}`" in source
    assert "重新索引已请求，embedding provider 暂不可用" in source

    assert "function showDisableUnavailable" in source
    assert "当前知识 API 不支持禁用，未执行任何变更" in source
    assert "/knowledge/reindex" not in source
    assert "/knowledge/disable" not in source
    assert "/disable`" not in source


def test_settings_index_copy_uses_agent_workspace_labels() -> None:
    source = _source(SETTINGS_PAGE)

    for label in [
        "模型与 Agent",
        "接口文档",
        "测试环境",
        "用例资产",
        "RAG 知识库",
        "Agent 设置",
    ]:
        assert label in source

    for old_copy in ["模型管理", "文档管理", "环境管理", "用例库", "系统设置"]:
        assert old_copy not in source

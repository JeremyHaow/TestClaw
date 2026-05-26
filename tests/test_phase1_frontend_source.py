import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UI_DIR = ROOT / "frontend/src/components/ui"
SIDEBAR = ROOT / "frontend/src/components/AppSidebar.vue"
HEADER = ROOT / "frontend/src/components/AppHeader.vue"
ADMIN_LAYOUT = ROOT / "frontend/src/layouts/AdminLayout.vue"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_phase1_ui_base_components_exist_and_use_vue_tailwind() -> None:
    component_names = [
        "TcButton.vue",
        "TcCard.vue",
        "TcBadge.vue",
        "TcOptionCard.vue",
        "TcStepBar.vue",
        "TcTextarea.vue",
    ]

    for component_name in component_names:
        source = _source(UI_DIR / component_name)
        assert '<script setup lang="ts">' in source
        assert "<template>" in source
        assert "primary" in source
        assert "secondary" in source
        assert "ghost" in source
        assert "danger" in source
        assert re.search(r"(rounded|border|bg-|text-)", source)
        assert not re.search(r"(element-plus|naive-ui|ant-design-vue|@headlessui|radix)", source)


def test_phase1_sidebar_navigation_groups_and_labels_are_locked() -> None:
    source = _source(SIDEBAR)

    assert "label: 'Workspace'" in source
    assert "label: 'Assets'" in source
    assert "label: 'Settings'" in source

    for label in [
        "智能计划",
        "任务委派",
        "运行历史",
        "质量记忆",
        "接口文档",
        "测试环境",
        "用例资产",
        "模型与 Agent",
        "RAG 知识库",
    ]:
        assert label in source

    assert "计划模式" not in source
    assert "手动模式" not in source
    assert "mobileOpen" in source
    assert "collapsed" in source
    assert "bg-blue-600 text-white" in source


def test_phase1_header_right_actions_and_mobile_menu_are_locked() -> None:
    source = _source(HEADER)

    assert 'aria-label="打开导航菜单"' in source
    assert "goTo('/history')" in source
    assert "历史" in source
    assert "已认证" in source
    assert "admin" in source
    assert "退出" in source
    assert "新建运行" not in source


def test_phase1_layout_shell_uses_new_page_background() -> None:
    source = _source(ADMIN_LAYOUT)

    assert "bg-[#F5F7FB]" in source or "bg-[#F7F9FC]" in source
    assert "bg-[#f4f6f5]" not in source

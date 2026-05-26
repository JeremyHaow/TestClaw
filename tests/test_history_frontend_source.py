from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HISTORY_PAGE = ROOT / "frontend/src/pages/HistoryPage.vue"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_history_page_new_ui_labels_are_visible() -> None:
    source = _source(HISTORY_PAGE)

    for label in [
        "运行历史",
        "总运行次数",
        "成功率",
        "发现问题数",
        "平均耗时",
        "证据完整率",
        "过滤器",
        "今天",
        "7 天",
        "30 天",
        "自定义",
        "查看详情",
        "重新运行",
        "导出报告",
    ]:
        assert label in source

    assert "筛选" in source


def test_history_page_preserves_existing_runs_hooks() -> None:
    source = _source(HISTORY_PAGE)

    for hook in [
        "api.get('/runs', { params })",
        "api.delete(`/runs/${id}`)",
        "['queued', 'running'].includes(statusValue(r))",
        "<Pagination",
        "page_size",
        "filterStatus",
        "filterType",
        "filterWindow",
        "params.search",
        "created_after",
        "created_before",
    ]:
        assert hook in source


def test_history_page_adds_rerun_and_export_actions() -> None:
    source = _source(HISTORY_PAGE)

    for hook in [
        "function viewRunDetails",
        "router.push(`/runs/${run.id}`)",
        "api.post(`/runs/${run.id}/rerun`)",
        "function exportRunReport",
        "/triage-export",
        "format: 'markdown'",
    ]:
        assert hook in source


def test_history_page_renders_run_cards_from_loaded_runs() -> None:
    source = _source(HISTORY_PAGE)

    assert 'v-for="run in visibleRuns"' in source
    assert "runs.value.filter" in source
    assert 'data-testid="history-run-card"' in source
    assert "runSnippets(run)" in source
    assert "<table" not in source.lower()

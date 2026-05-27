from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN_DETAIL_PAGE = ROOT / "frontend/src/pages/RunDetailPage.vue"
AGENT_COMPONENT_DIR = ROOT / "frontend/src/components/agent"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _run_detail_sources() -> str:
    component_names = [
        "AgentTimeline.vue",
        "AgentCurrentActionCard.vue",
        "AgentEvidenceCard.vue",
        "AgentInterventionDrawer.vue",
        "AgentRunSummary.vue",
    ]
    sources = [_source(RUN_DETAIL_PAGE)]
    sources.extend(_source(AGENT_COMPONENT_DIR / name) for name in component_names)
    return "\n".join(sources)


def test_run_detail_uses_named_agent_cockpit_components() -> None:
    source = _source(RUN_DETAIL_PAGE)

    for component in [
        "AgentTimeline",
        "AgentCurrentActionCard",
        "AgentEvidenceCard",
        "AgentInterventionDrawer",
        "AgentRunSummary",
    ]:
        assert (AGENT_COMPONENT_DIR / f"{component}.vue").exists()
        assert f"import {component}" in source
        assert f"<{component}" in source


def test_run_detail_labels_remain_visible_after_extraction() -> None:
    source = _run_detail_sources()

    for label in [
        "Agent Cockpit",
        "当前动作",
        "测试计划",
        "测试用例",
        "执行日志",
        "证据",
        "报告",
        "运行摘要",
    ]:
        assert label in source

    assert "人工干预" in source or "补充上下文" in source


def test_run_detail_sse_and_intervention_hooks_remain_in_page() -> None:
    source = _source(RUN_DETAIL_PAGE)

    for hook in [
        "EventSource(apiUrl(`/runs/${runId}/stream`, { token }))",
        "function connectSSE",
        "function disconnectSSE",
        "api.post(`/runs/${route.params.id}/interventions`,",
        "submitInterventionRerun",
        "/stream",
        "/interventions",
    ]:
        assert hook in source


def test_agent_components_keep_run_detail_as_side_effect_owner() -> None:
    page_source = _source(RUN_DETAIL_PAGE)
    components_source = _run_detail_sources().replace(page_source, "")

    assert "@cancel=\"cancelRun\"" in page_source
    assert "@submit=\"submitInterventionRerun\"" in page_source
    assert "v-model=\"interventionText\"" in page_source
    assert "v-model:cancel-current=\"interventionCancelCurrent\"" in page_source

    for side_effect in [
        "new EventSource",
        "api.post",
        "api.get",
        "router.push",
        "localStorage",
    ]:
        assert side_effect not in components_source

    assert "defineProps" in components_source
    assert "defineEmits" in components_source
    assert "<slot" in components_source


def test_run_detail_wires_agent_protocol_records_into_cockpit() -> None:
    source = _source(RUN_DETAIL_PAGE)

    for key in [
        "agent_tool_calls",
        "agent_observations",
        "agent_evidence",
        "agent_protocol_evaluations",
        "agent_protocol_summary",
        "agent_retry_counts",
        "agent_retry_feedback",
        "agent_human_question",
    ]:
        assert f"'{key}'" in source

    for symbol in [
        "protocolTimelineItems",
        "hasProtocolSurface",
        'v-if="hasProtocolSurface && !triageSummary"',
        "agentEvaluationRecords",
        "recentProtocolObservations",
        "recentProtocolEvidence",
        "protocolFailureCount",
    ]:
        assert symbol in source


def test_agent_timeline_marks_protocol_statuses() -> None:
    source = _source(AGENT_COMPONENT_DIR / "AgentTimeline.vue")

    for status in [
        "needs_retry",
        "needs_replan",
        "needs_human",
        "sufficient",
        "insufficient",
    ]:
        assert status in source

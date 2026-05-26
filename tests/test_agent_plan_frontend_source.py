import re
from pathlib import Path


PAGE = Path(__file__).resolve().parents[1] / "frontend/src/pages/AgentPlanPage.vue"
QUESTION_CARD = Path(__file__).resolve().parents[1] / "frontend/src/components/agent/AgentQuestionCard.vue"
PLAN_DRAFT = Path(__file__).resolve().parents[1] / "frontend/src/components/agent/AgentPlanDraft.vue"
CHAT_INPUT = Path(__file__).resolve().parents[1] / "frontend/src/components/agent/AgentChatInput.vue"
AGENT_PLAN_TYPES = Path(__file__).resolve().parents[1] / "frontend/src/types/agentPlan.ts"
LOGIN_PAGE = Path(__file__).resolve().parents[1] / "frontend/src/pages/LoginPage.vue"
ROUTER = Path(__file__).resolve().parents[1] / "frontend/src/router/index.ts"


def _template_source(path: Path = PAGE) -> str:
    source = path.read_text(encoding="utf-8")
    return source.split("<template>", 1)[1].split("</template>", 1)[0]


def _combined_agent_plan_source() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in [PAGE, QUESTION_CARD, PLAN_DRAFT, CHAT_INPUT, AGENT_PLAN_TYPES]
    )


def _button_visible_text(template: str, click_handler: str) -> str:
    pattern = re.compile(rf"<button\b(?=[^>]*@click=\"{click_handler}\")[\s\S]*?</button>")
    match = pattern.search(template)
    assert match is not None
    without_tags = re.sub(r"<[^>]+>", " ", match.group(0))
    return re.sub(r"\s+", " ", without_tags).strip()


def test_plan_card_action_labels_are_exactly_chinese() -> None:
    template = _template_source(PLAN_DRAFT)

    assert _button_visible_text(template, "emit\\('reject'\\)") == "拒绝"
    assert _button_visible_text(template, "emit\\('execute'\\)") == "立即执行"


def test_agent_plan_page_static_template_text_is_localized() -> None:
    template = "\n".join(_template_source(path) for path in [PAGE, QUESTION_CARD, PLAN_DRAFT, CHAT_INPUT])
    without_mustache = re.sub(r"{{[\s\S]*?}}", " ", template)
    visible_text = re.sub(r"<[^>]+>", " ", without_mustache)
    visible_text = re.sub(r"\s+", " ", visible_text)

    assert re.search(r"\b[A-Za-z]{2,}\b", visible_text) is None


def test_agent_plan_is_default_entry_after_login_and_root_redirects() -> None:
    login_source = LOGIN_PAGE.read_text(encoding="utf-8")
    router_source = ROUTER.read_text(encoding="utf-8")

    assert "router.push('/agent-plan')" in login_source
    assert "router.push('/run')" not in login_source
    assert "redirect: '/agent-plan'" in router_source
    assert "return '/agent-plan'" in router_source


def test_agent_plan_uses_streaming_and_live_message_controls() -> None:
    source = _combined_agent_plan_source()

    assert "streamPlannerTurn" in source
    assert "/messages/stream" in source
    assert "/stream`" in source
    assert "eventName === 'token'" in source
    assert "process_events" in source
    assert "deleteSession" in source
    assert "deleteMessage" in source
    assert "startEditMessage" in source
    assert "resendEditedMessage" in source
    assert "Executed plan cannot be changed" in source
    assert "!canModifyActiveSession" in source
    assert "PlannerQuestionOption" in source
    assert "messageQuestionOptions" in source
    assert ".slice(0, 2)" in source
    assert "intakeSteps" in source
    assert "测试目标" in source
    assert "覆盖范围" in source
    assert "登录方式/凭证" in source
    assert "安全边界" in source
    assert "成功标准" in source
    assert "计划草案" in source
    assert '@select="selectIntakeChoice"' in source
    assert "AgentQuestionCard" in source
    assert "AgentPlanDraft" in source
    assert "AgentChatInput" in source
    assert "selectedIntakeChoices" in source
    assert "currentSupplementText" in source
    assert "intakeControlsDisabled" in source
    assert ':disabled="intakeControlsDisabled"' in source
    assert "skipCurrentStep" in source
    assert "deferCurrentStep" in source
    assert "continueIntake" in source
    assert "applyChoiceToDraft" not in source
    assert "sendChoice" not in source
    assert "await sendMessage()" not in source
    assert "draftInput" in source
    assert "editingRollbackSnapshot" in source
    assert "applyEditRollback" in source
    assert "sourceMessages.slice(0, index + 1)" in source


def test_agent_plan_consumes_quality_memory_handoff_once() -> None:
    source = PAGE.read_text(encoding="utf-8")

    for hook in [
        "useRoute",
        "importedQualityMemoryPlanContent",
        "queryText(route.query.from) !== 'quality-memory'",
        "redactImportedPlanContext",
        "clearImportedQualityMemoryQuery",
        "delete nextQuery.context",
        "await createSession()",
        "await submitPlannerContent(importedContent, importedContent)",
        "onMounted(initializePage)",
    ]:
        assert hook in source

import re
from pathlib import Path


PAGE = Path(__file__).resolve().parents[1] / "frontend/src/pages/AgentPlanPage.vue"
LOGIN_PAGE = Path(__file__).resolve().parents[1] / "frontend/src/pages/LoginPage.vue"
ROUTER = Path(__file__).resolve().parents[1] / "frontend/src/router/index.ts"


def _template_source() -> str:
    source = PAGE.read_text(encoding="utf-8")
    return source.split("<template>", 1)[1].split("</template>", 1)[0]


def _button_visible_text(template: str, click_handler: str) -> str:
    pattern = re.compile(rf"<button\b(?=[^>]*@click=\"{click_handler}\")[\s\S]*?</button>")
    match = pattern.search(template)
    assert match is not None
    without_tags = re.sub(r"<[^>]+>", " ", match.group(0))
    return re.sub(r"\s+", " ", without_tags).strip()


def test_plan_card_action_labels_are_exactly_chinese() -> None:
    template = _template_source()

    assert _button_visible_text(template, "rejectPlan") == "拒绝"
    assert _button_visible_text(template, "executePlan") == "立即执行"


def test_agent_plan_page_static_template_text_is_localized() -> None:
    template = _template_source()
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
    source = PAGE.read_text(encoding="utf-8")

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
    assert "latestOptionMessageId" in source
    assert ".slice(0, 2)" in source
    assert "applyChoiceToDraft(option)" in source
    assert "sendChoice" not in source
    assert "await sendMessage()" not in source
    assert "draftInput" in source
    assert "editingRollbackSnapshot" in source
    assert "applyEditRollback" in source
    assert "sourceMessages.slice(0, index + 1)" in source

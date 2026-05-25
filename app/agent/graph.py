from langgraph.graph import END, StateGraph

from app.agent.nodes import (
    analyzer,
    api_runner,
    coder,
    executor,
    execution_evaluator,
    healer,
    input_classifier,
    knowledge_retriever,
    knowledge_sink,
    planner,
    reporter,
    source_loader,
    tc_generator,
    ui_login,
    ui_runner,
    ui_test_planner,
)
from app.agent.state import AgentState


def _after_tc_generator(state: AgentState) -> str:
    """Route based on test type and input type."""
    input_type = state.get("input_type", "unknown")
    test_type = (state.get("test_type") or "auto").lower()
    has_api_schema = bool(state.get("parsed_api_schema"))
    has_api_cases = bool(state.get("api_cases"))
    has_api_target = has_api_schema or has_api_cases or bool(state.get("base_url_override"))
    has_ui_target = input_type == "url" or bool(state.get("ui_seed_url"))

    # Explicit API: go to api_runner
    if test_type == "api":
        return "api_runner"

    # Explicit UI: go through login → planner → runner
    if test_type == "ui":
        return "ui_login"

    # Full: api first, then login → planner → runner
    if test_type == "full":
        return "api_runner"

    # Auto mode
    if has_api_target and has_ui_target:
        return "api_runner"
    if input_type in ("swagger_url", "swagger_json", "swagger_yaml") and has_api_schema:
        return "api_runner"
    if input_type == "url":
        return "ui_login"

    if has_api_schema:
        return "api_runner"

    return "ui_login"


def _after_api_runner(state: AgentState) -> str:
    """After API runner, chain to UI login when the run has a UI target."""
    test_type = (state.get("test_type") or "auto").lower()
    input_type = state.get("input_type", "unknown")
    has_ui_target = input_type == "url" or bool(state.get("ui_seed_url"))
    if test_type == "full":
        return "ui_login"
    if test_type == "auto" and has_ui_target:
        return "ui_login"
    return "reporter"


def _after_execution_evaluator(state: AgentState) -> str:
    return execution_evaluator.route_after_evaluation(state)


def _after_ui_login(state: AgentState) -> str:
    setup_required = bool((state.get("setup_instructions") or state.get("login_instructions") or "").strip())
    login_verified = state.get("login_verified")
    setup_result = state.get("setup_result") or state.get("login_result") or {}
    if setup_required and setup_result.get("required") and login_verified is False:
        return "reporter"
    return "ui_test_planner"


def _after_coder(state: AgentState) -> str:
    return "executor"


def _after_executor(state: AgentState) -> str:
    return "analyzer"


def _after_analyzer(state: AgentState) -> str:
    result = state.get("execution_result") or {}
    if result.get("status_code") == 0:
        return "reporter"
    retry = state.get("retry_count", 0)
    if retry >= 3:
        return "reporter"
    return "healer"


def _after_healer(state: AgentState) -> str:
    return "executor"


def build_graph():
    graph = StateGraph(AgentState)

    # Core workflow nodes
    graph.add_node("input_classifier", input_classifier.run)
    graph.add_node("source_loader", source_loader.run)
    graph.add_node("knowledge_retriever", knowledge_retriever.run)
    graph.add_node("planner", planner.run)
    graph.add_node("tc_generator", tc_generator.run)
    graph.add_node("api_runner", api_runner.run)
    graph.add_node("execution_evaluator", execution_evaluator.run)
    graph.add_node("ui_login", ui_login.run)
    graph.add_node("ui_test_planner", ui_test_planner.run)
    graph.add_node("ui_runner", ui_runner.run)
    graph.add_node("reporter", reporter.run)

    # Legacy nodes
    graph.add_node("coder", coder.run)
    graph.add_node("executor", executor.run)
    graph.add_node("analyzer", analyzer.run)
    graph.add_node("healer", healer.run)
    graph.add_node("knowledge_sink", knowledge_sink.run)

    # Entry point
    graph.set_entry_point("input_classifier")

    # Linear edges
    graph.add_edge("input_classifier", "source_loader")
    graph.add_edge("source_loader", "knowledge_retriever")
    graph.add_edge("knowledge_retriever", "planner")
    graph.add_edge("planner", "tc_generator")
    graph.add_conditional_edges(
        "ui_login",
        _after_ui_login,
        {
            "ui_test_planner": "ui_test_planner",
            "reporter": "reporter",
        },
    )
    graph.add_edge("ui_test_planner", "ui_runner")
    graph.add_edge("ui_runner", "execution_evaluator")

    # Conditional: tc_generator → api_runner | ui_login | coder
    graph.add_conditional_edges(
        "tc_generator",
        _after_tc_generator,
        {
            "api_runner": "api_runner",
            "ui_login": "ui_login",
            "coder": "coder",
        },
    )

    graph.add_edge("api_runner", "execution_evaluator")
    graph.add_conditional_edges(
        "execution_evaluator",
        _after_execution_evaluator,
        {
            "tc_generator": "tc_generator",
            "ui_test_planner": "ui_test_planner",
            "ui_login": "ui_login",
            "reporter": "reporter",
        },
    )

    # Legacy flow
    graph.add_edge("coder", "executor")
    graph.add_edge("executor", "analyzer")
    graph.add_conditional_edges(
        "analyzer",
        _after_analyzer,
        {"reporter": "reporter", "healer": "healer"},
    )
    graph.add_edge("healer", "executor")

    # Reporter → knowledge_sink → END
    graph.add_edge("reporter", "knowledge_sink")
    graph.add_edge("knowledge_sink", END)

    return graph.compile()


agent_graph = build_graph()

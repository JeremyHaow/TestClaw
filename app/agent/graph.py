from langgraph.graph import END, StateGraph

from app.agent.nodes import (
    analyzer,
    api_runner,
    coder,
    executor,
    healer,
    input_classifier,
    knowledge_sink,
    planner,
    reporter,
    source_loader,
    tc_generator,
    ui_runner,
)
from app.agent.state import AgentState


def _after_input_classifier(state: AgentState) -> str:
    """Route to source_loader after classification."""
    return "source_loader"


def _after_source_loader(state: AgentState) -> str:
    """Route to planner after loading source."""
    return "planner"


def _after_planner(state: AgentState) -> str:
    """Route to case generator after planning."""
    return "tc_generator"


def _after_tc_generator(state: AgentState) -> str:
    """Route based on test type and available schema."""
    input_type = state.get("input_type", "unknown")
    test_type = state.get("test_type", "auto")

    has_api_schema = bool(state.get("parsed_api_schema"))
    has_ui_url = bool(state.get("ui_seed_url") or state.get("target_url"))

    # If explicit test_type, respect it
    if test_type == "api":
        return "api_runner"
    if test_type == "ui":
        return "ui_runner"

    # Auto mode: route based on input type and available data
    if input_type in ("swagger_url", "swagger_json", "swagger_yaml") and has_api_schema:
        return "api_runner"
    if input_type == "url":
        return "ui_runner"

    # Default: try API first if we have schema, otherwise UI
    if has_api_schema:
        return "api_runner"
    if has_ui_url:
        return "ui_runner"

    # Fallback to legacy coder → executor flow
    return "coder"


def _after_api_runner(state: AgentState) -> str:
    """After API runner, check if we should also run UI tests."""
    test_type = state.get("test_type", "auto")
    input_type = state.get("input_type", "unknown")

    # In auto mode with swagger input, also try UI if we have a URL
    if test_type == "auto" and input_type in ("swagger_url", "swagger_json", "swagger_yaml"):
        ui_url = state.get("ui_seed_url") or state.get("target_url")
        if ui_url:
            return "ui_runner"

    return "reporter"


def _after_ui_runner(state: AgentState) -> str:
    """After UI runner, go to reporter."""
    return "reporter"


def _after_coder(state: AgentState) -> str:
    """Legacy flow: coder → executor."""
    return "executor"


def _after_executor(state: AgentState) -> str:
    """Legacy flow: executor → analyzer."""
    return "analyzer"


def _after_analyzer(state: AgentState) -> str:
    """Legacy flow: analyzer routing."""
    result = state.get("execution_result") or {}
    if result.get("status_code") == 0:
        return "reporter"
    retry = state.get("retry_count", 0)
    if retry >= 3:
        return "reporter"
    return "healer"


def _after_healer(state: AgentState) -> str:
    """Legacy flow: healer → executor (retry)."""
    return "executor"


def build_graph():
    graph = StateGraph(AgentState)

    # New workflow nodes
    graph.add_node("input_classifier", input_classifier.run)
    graph.add_node("source_loader", source_loader.run)
    graph.add_node("planner", planner.run)
    graph.add_node("tc_generator", tc_generator.run)
    graph.add_node("api_runner", api_runner.run)
    graph.add_node("ui_runner", ui_runner.run)
    graph.add_node("reporter", reporter.run)

    # Legacy nodes (kept for backward compat)
    graph.add_node("coder", coder.run)
    graph.add_node("executor", executor.run)
    graph.add_node("analyzer", analyzer.run)
    graph.add_node("healer", healer.run)
    graph.add_node("knowledge_sink", knowledge_sink.run)

    # Entry point
    graph.set_entry_point("input_classifier")

    # New workflow edges
    graph.add_edge("input_classifier", "source_loader")
    graph.add_edge("source_loader", "planner")
    graph.add_edge("planner", "tc_generator")

    # Conditional routing after case generation
    graph.add_conditional_edges(
        "tc_generator",
        _after_tc_generator,
        {
            "api_runner": "api_runner",
            "ui_runner": "ui_runner",
            "coder": "coder",  # legacy fallback
        },
    )

    # API runner → UI runner (auto mode) or → reporter
    graph.add_conditional_edges(
        "api_runner",
        _after_api_runner,
        {
            "ui_runner": "ui_runner",
            "reporter": "reporter",
        },
    )

    # UI runner → reporter
    graph.add_edge("ui_runner", "reporter")

    # Legacy flow edges
    graph.add_edge("coder", "executor")
    graph.add_edge("executor", "analyzer")
    graph.add_conditional_edges(
        "analyzer",
        _after_analyzer,
        {
            "reporter": "reporter",
            "healer": "healer",
        },
    )
    graph.add_edge("healer", "executor")

    # Reporter → knowledge_sink → END
    graph.add_edge("reporter", "knowledge_sink")
    graph.add_edge("knowledge_sink", END)

    return graph.compile()


agent_graph = build_graph()

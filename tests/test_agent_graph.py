from app.agent.graph import (
    _after_agent_supervisor,
    _after_api_runner,
    _after_execution_evaluator,
    _after_tc_generator,
    _after_ui_login,
    build_graph,
)


def test_graph_routes_source_loader_through_rag_before_planning() -> None:
    graph = build_graph().get_graph()
    edges = {(edge.source, edge.target) for edge in graph.edges}

    assert ("source_loader", "mission_planner") in edges
    assert ("mission_planner", "knowledge_retriever") in edges
    assert ("knowledge_retriever", "planner") in edges
    assert ("planner", "agent_supervisor") in edges
    assert ("agent_supervisor", "tc_generator") in edges
    assert ("agent_supervisor", "ui_login") in edges
    assert ("source_loader", "planner") not in edges
    assert ("planner", "tc_generator") not in edges
    assert ("api_runner", "execution_evaluator") in edges
    assert ("ui_runner", "execution_evaluator") in edges
    assert ("ui_runner", "reporter") not in edges


def test_legacy_coder_executor_route_is_not_active() -> None:
    graph = build_graph().get_graph()
    node_ids = set(graph.nodes)
    edges = {(edge.source, edge.target) for edge in graph.edges}

    assert {"coder", "executor", "analyzer", "healer"}.isdisjoint(node_ids)
    assert not any(
        node in {"coder", "executor", "analyzer", "healer"}
        for edge in edges
        for node in edge
    )


def test_after_tc_generator_routes_ui_runs_through_login_chain() -> None:
    assert _after_tc_generator({"test_type": "ui", "input_type": "url"}) == "ui_login"
    assert _after_tc_generator(
        {
            "test_type": "auto",
            "input_type": "url",
            "base_url_override": "https://api.example.test",
            "ui_seed_url": "https://web.example.test",
            "api_cases": [{"title": "smoke"}],
        }
    ) == "api_runner"


def test_after_agent_supervisor_routes_ui_only_runs_to_browser_chain() -> None:
    assert _after_agent_supervisor({"test_type": "ui", "input_type": "url"}) == "ui_login"
    assert _after_agent_supervisor(
        {
            "test_type": "auto",
            "input_type": "url",
            "ui_seed_url": "https://web.example.test",
        }
    ) == "ui_login"
    assert _after_agent_supervisor(
        {
            "test_type": "auto",
            "input_type": "url",
            "base_url_override": "https://api.example.test",
            "ui_seed_url": "https://web.example.test",
        }
    ) == "tc_generator"


def test_after_api_runner_routes_back_to_ui_for_auto_and_full_runs() -> None:
    assert _after_api_runner({"test_type": "full", "input_type": "url"}) == "ui_login"
    assert _after_api_runner(
        {
            "test_type": "auto",
            "input_type": "url",
            "ui_seed_url": "https://web.example.test",
        }
    ) == "ui_login"
    assert _after_api_runner({"test_type": "api", "input_type": "swagger_url"}) == "reporter"


def test_after_execution_evaluator_uses_bounded_next_node() -> None:
    assert _after_execution_evaluator({"agent_next_node": "api_runner"}) == "api_runner"
    assert _after_execution_evaluator({"agent_next_node": "tc_generator"}) == "tc_generator"
    assert _after_execution_evaluator({"agent_next_node": "ui_runner"}) == "ui_runner"
    assert _after_execution_evaluator({"agent_next_node": "ui_test_planner"}) == "ui_test_planner"
    assert _after_execution_evaluator({"agent_next_node": "ui_login"}) == "ui_login"
    assert _after_execution_evaluator({"agent_next_node": "unknown"}) == "reporter"


def test_after_ui_login_routes_failed_required_setup_to_reporter() -> None:
    assert _after_ui_login({
        "setup_instructions": "prepare browser state",
        "setup_result": {"required": True},
        "login_verified": False,
    }) == "reporter"
    assert _after_ui_login({
        "setup_instructions": "prepare browser state",
        "setup_result": {"required": True},
        "login_verified": True,
    }) == "ui_test_planner"
    assert _after_ui_login({
        "setup_instructions": "read-only scope only",
        "setup_result": {"required": False},
        "login_verified": None,
    }) == "ui_test_planner"
    assert _after_ui_login({"setup_instructions": None, "login_verified": None}) == "ui_test_planner"

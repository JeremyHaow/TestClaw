from app.agent.graph import _after_api_runner, _after_tc_generator, _after_ui_login, build_graph


def test_graph_routes_source_loader_through_rag_before_planning() -> None:
    graph = build_graph().get_graph()
    edges = {(edge.source, edge.target) for edge in graph.edges}

    assert ("source_loader", "knowledge_retriever") in edges
    assert ("knowledge_retriever", "planner") in edges
    assert ("source_loader", "planner") not in edges


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

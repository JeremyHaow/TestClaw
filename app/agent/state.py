from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph import add_messages


class AgentState(TypedDict, total=False):
    # --- Core identifiers ---
    task_id: str
    objective: str
    target_url: str
    api_doc_id: str | None
    environment_id: str | None
    db_session: Any  # AsyncSession

    # --- Input classification ---
    input_type: Literal["swagger_url", "swagger_json", "swagger_yaml", "url", "unknown"]
    source_input: str  # the raw user input (URL or text)

    # --- Parsed content ---
    document_content: str | None  # raw fetched document text
    parsed_api_schema: list[dict] | None  # rich endpoint descriptors
    ui_seed_url: str | None  # URL for UI testing

    # --- Test planning ---
    test_type: Literal["ui", "api", "auto", "functional", "full", "suite"]
    api_plan: dict | None  # structured API test plan
    ui_plan: dict | None  # structured UI test plan
    test_plan: list[dict] | None  # legacy combined plan (fallback)

    # --- Test cases ---
    api_cases: list[dict] | None  # API test cases with request templates
    ui_cases: list[dict] | None  # UI test cases with playwright-cli hints
    test_cases: list[dict] | None  # legacy combined cases (fallback)

    # --- Execution results ---
    api_execution_result: dict | None  # API runner results
    ui_execution_result: dict | None  # UI runner results
    execution_result: dict | None  # legacy combined result (fallback)

    # --- Code generation (legacy, kept for backward compat) ---
    generated_code: str | None

    # --- Report ---
    final_report: dict | None  # aggregated report from reporter
    artifacts: dict | None  # screenshots, traces, logs
    tool_registry: dict | None  # available automation tools and skills
    skill_plan: list[dict] | None  # selected skills for this run
    tool_calls: list[dict] | None  # auditable tool invocations
    tool_summary: dict | None  # aggregate tool-call counts
    agent_mission_plan: dict | None  # mission-level plan-execute control artifact
    agent_roster: list[dict] | None  # active multi-agent roles for this mission
    agent_delegation_trace: list[dict] | None  # supervisor-to-role task delegation records
    agent_react_trace: list[dict] | None  # visible reason/action/observation trace
    evidence_evaluation: dict | None  # latest agent quality gate decision
    agent_evaluations: list[dict] | None  # bounded evaluation/replan history
    agent_attempt_history: list[dict] | None  # compact summaries of replaced attempts
    agent_execution_stage: Literal["api", "ui"] | None
    agent_next_node: str | None
    agent_replan_counts: dict[str, int] | None
    agent_replan_feedback: str | None
    agent_case_diagnostics: list[dict] | None

    # --- UI Login ---
    setup_instructions: str | None  # natural language pre-test setup/context from user
    setup_result: dict | None  # setup execution summary
    login_instructions: str | None  # deprecated alias for setup_instructions
    ui_login_snapshot: str | None  # page snapshot after successful login
    login_playwright_commands: list[str] | None  # commands used for login
    login_result: dict | None  # login execution summary
    login_verified: bool | None  # whether login appears successful
    login_verification_reason: str | None  # human-readable verification reason
    ui_login_screenshot: str | None  # final login screenshot evidence
    ui_captcha_result: dict | None
    authenticated_ui_context: dict | None  # post-login/authenticated UI context summary
    ui_reproducible_script: str | None  # full reproducible playwright-cli script
    ui_execution_context_plan: list[dict] | None  # per-case execution context decisions

    # --- Analysis context ---
    scene_hints: list[dict] | None  # detected API/product scenes
    auth_chain: dict | None  # summarized auth chain for planning/case generation

    # --- Agent control ---
    retry_count: int
    last_error: str | None
    old_locator: str | None
    rag_context: str | None
    rag_retrieval: dict | None
    bug_report: dict | None
    messages: Annotated[list[BaseMessage], add_messages]
    workflow_steps: list[dict]
    progress_events: list[dict]
    current_step: dict | None
    auth_headers: dict[str, str] | None
    auth_config: dict[str, Any] | None
    auth_mode: Literal["auto", "manual", "none_confirmed"] | None
    captcha_mode: Literal["none", "static", "dynamic"] | None
    auth_credentials: dict[str, str] | None
    auth_preflight: dict[str, Any] | None
    custom_headers: dict[str, str] | None
    base_url_override: str | None
    api_execution_policy: Literal["safe_read_only", "safe_with_auth", "write_allowed"] | None
    allow_out_of_schema_api_cases: bool | None
    api_request_selection: dict[str, Any] | None
    api_path_prefix_rewrite: dict[str, str] | None

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
    test_type: Literal["ui", "api", "auto"]
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

    # --- Agent control ---
    retry_count: int
    last_error: str | None
    old_locator: str | None
    rag_context: str | None
    bug_report: dict | None
    messages: Annotated[list[BaseMessage], add_messages]
    workflow_steps: list[dict]

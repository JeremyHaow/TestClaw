import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_0005 = ROOT / "alembic/versions/0005_agent_planning_sessions.py"
MIGRATION_0006 = ROOT / "alembic/versions/0006_run_operational_tables.py"
MAIN = ROOT / "app/main.py"

REQUIRED_OPERATIONAL_TABLES = {
    "agent_plans",
    "run_events",
    "run_interventions",
    "run_tool_calls",
    "run_evidence",
    "run_findings",
    "target_memories",
    "artifacts",
}


def test_run_operational_migration_covers_required_tables() -> None:
    assert MIGRATION_0006.exists()
    source = MIGRATION_0006.read_text()

    assert 'revision: str = "0006_run_operational_tables"' in source
    assert 'down_revision: str | None = "0005_agent_planning_sessions"' in source
    assert "def _has_table" in source
    assert "if_not_exists=True" in source
    assert 'op.create_table(\n            "runs"' not in source
    for table_name in REQUIRED_OPERATIONAL_TABLES:
        assert f'"{table_name}"' in source


def test_model_metadata_includes_run_operational_tables() -> None:
    import app.models  # noqa: F401
    from app.database import Base

    table_names = set(Base.metadata.tables)
    assert REQUIRED_OPERATIONAL_TABLES <= table_names
    assert {"agent_planning_sessions", "agent_planning_messages"} <= table_names

    run_event_indexes = {index.name for index in Base.metadata.tables["run_events"].indexes}
    target_memory_indexes = {
        index.name: index for index in Base.metadata.tables["target_memories"].indexes
    }
    assert "idx_run_events_run_seq" in run_event_indexes
    assert target_memory_indexes["ix_target_memories_target_key"].unique is True


def test_main_does_not_unconditionally_create_all() -> None:
    source = MAIN.read_text()
    tree = ast.parse(source)
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    create_all_calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "run_sync":
            continue
        if not node.args:
            continue
        if ast.get_source_segment(source, node.args[0]) == "Base.metadata.create_all":
            create_all_calls.append(node)

    assert create_all_calls
    for call in create_all_calls:
        cursor: ast.AST | None = call
        guarded_by_sqlite_check = False
        while cursor in parents:
            cursor = parents[cursor]
            if isinstance(cursor, ast.If):
                test_source = ast.get_source_segment(source, cursor.test) or ""
                if "_should_create_all_on_startup(settings.DATABASE_URL)" in test_source:
                    guarded_by_sqlite_check = True
                    break
        assert guarded_by_sqlite_check

    assert 'drivername.startswith("sqlite")' in source


def test_agent_plan_sessions_are_existing_tables_and_agent_plans_is_new() -> None:
    prior_source = MIGRATION_0005.read_text()
    current_source = MIGRATION_0006.read_text()

    assert '"agent_planning_sessions"' in prior_source
    assert '"agent_planning_messages"' in prior_source
    assert '"agent_plans"' in current_source

    for table_name in (
        "agent_planning_sessions",
        "agent_planning_messages",
        "agent_plan_sessions",
        "agent_plan_messages",
    ):
        assert f'"{table_name}"' not in current_source

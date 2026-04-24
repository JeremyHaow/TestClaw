from langchain_core.tools import tool


@tool
def inject_mock_dependency(target: str, mock_value: dict) -> dict:
    """Return a structured mock definition for an external dependency."""
    return {"target": target, "mock_value": mock_value, "applied": True}

import zipfile
from pathlib import Path

from langchain_core.tools import tool


@tool
def extract_trace_data(trace_path: str) -> dict:
    """Extract basic file listing details from a Playwright trace zip."""
    path = Path(trace_path)
    if not path.exists():
        return {"files": [], "errors": ["trace file not found"]}
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
    return {"files": names, "errors": []}

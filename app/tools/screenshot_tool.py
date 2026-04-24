import base64
from pathlib import Path

from langchain_core.tools import tool


@tool
def take_screenshot(image_path: str) -> str:
    """Read an image file and return its base64 content."""
    path = Path(image_path)
    return base64.b64encode(path.read_bytes()).decode()

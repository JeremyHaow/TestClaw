from pathlib import Path

from langchain_core.tools import tool
from PIL import Image, ImageChops


@tool
def visual_regression_check(baseline_path: str, current_path: str) -> dict:
    """Compare two images and return a diff summary."""
    baseline = Image.open(Path(baseline_path))
    current = Image.open(Path(current_path))
    diff = ImageChops.difference(baseline, current)
    bbox = diff.getbbox()
    if bbox is None:
        return {"different": False, "bbox": None, "difference_pct": 0.0}
    changed_pixels = sum(1 for pixel in diff.getdata() if pixel != (0, 0, 0))
    total_pixels = diff.width * diff.height
    return {
        "different": True,
        "bbox": bbox,
        "difference_pct": round((changed_pixels / total_pixels) * 100, 4),
    }

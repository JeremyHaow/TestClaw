from html.parser import HTMLParser

from langchain_core.tools import tool


class _DOMCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.nodes: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        attributes = dict(attrs)
        if attributes.get("hidden") is not None:
            return
        if tag in {"script", "style"}:
            return
        descriptor = tag
        if "id" in attributes:
            descriptor += f"#{attributes['id']}"
        if "role" in attributes:
            descriptor += f"[role={attributes['role']}]"
        if "data-testid" in attributes:
            descriptor += f"[data-testid={attributes['data-testid']}]"
        self.nodes.append(descriptor)


@tool
def get_clean_dom(html: str) -> list[str]:
    """Extract a simplified interactable DOM tree from HTML."""
    parser = _DOMCollector()
    parser.feed(html)
    return parser.nodes

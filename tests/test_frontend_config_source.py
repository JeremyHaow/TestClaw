from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VITE_CONFIG = ROOT / "frontend/vite.config.ts"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_vite_dev_proxy_defaults_to_local_backend_and_allows_override() -> None:
    source = _source(VITE_CONFIG)

    assert "loadEnv" in source
    assert "VITE_DEV_API_PROXY_TARGET" in source
    assert "'http://127.0.0.1:8000'" in source
    assert "target: devApiProxyTarget" in source
    assert "target: 'http://api:8000'" not in source

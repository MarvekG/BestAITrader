from pathlib import Path

from uvicorn.config import Config


def test_reload_watches_only_source_and_config_dirs(monkeypatch):
    """确认后端热重载只监听源码和配置目录。"""
    backend_root = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(backend_root)
    config = Config(
        "app.main:app",
        reload=True,
        reload_dirs=["app", "config"],
        reload_includes=["*.json"],
    )

    assert set(config.reload_dirs) == {backend_root / "app", backend_root / "config"}
    assert all("/runtime" not in path.as_posix() for path in config.reload_dirs)

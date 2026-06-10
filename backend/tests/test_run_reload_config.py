from pathlib import Path

from uvicorn.config import Config
from uvicorn.supervisors.watchfilesreload import FileFilter

from run import RELOAD_EXCLUDES

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_reload_excludes_runtime_skill_files(monkeypatch):
    monkeypatch.chdir(BACKEND_ROOT)

    config = Config(
        "app.main:app",
        reload=True,
        reload_dirs=["app", "config"],
        reload_includes=["*.json"],
        reload_excludes=RELOAD_EXCLUDES,
    )
    file_filter = FileFilter(config)

    assert not file_filter(BACKEND_ROOT / "app/ai/agentic/skills_loader/skills/demo-skill/skill.json")
    assert not file_filter(BACKEND_ROOT / "app/ai/agentic/skills_loader/skills/demo-skill/scripts/echo_context.py")
    assert not file_filter(BACKEND_ROOT / "app/ai/agentic/tooling/news_plugins/external/custom/news.py")
    assert file_filter(BACKEND_ROOT / "app/ai/agentic/skills_loader/loader.py")
    assert file_filter(BACKEND_ROOT / "config/reload-trigger.json")

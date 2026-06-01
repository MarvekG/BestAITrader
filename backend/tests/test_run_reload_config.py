from uvicorn.config import Config
from uvicorn.supervisors.watchfilesreload import FileFilter

from run import BACKEND_ROOT, RELOAD_EXCLUDES


def test_reload_excludes_runtime_skill_files():
    config = Config(
        "app.main:app",
        reload=True,
        reload_dirs=[str(BACKEND_ROOT / "app"), str(BACKEND_ROOT / "config")],
        reload_includes=["*.json"],
        reload_excludes=RELOAD_EXCLUDES,
    )
    file_filter = FileFilter(config)

    assert not file_filter(BACKEND_ROOT / "app/ai/agentic/skills_loader/skills/demo-skill/skill.json")
    assert not file_filter(BACKEND_ROOT / "app/ai/agentic/skills_loader/skills/demo-skill/scripts/echo_context.py")
    assert not file_filter(BACKEND_ROOT / "app/ai/agentic/tooling/news_plugins/external/custom/news.py")
    assert file_filter(BACKEND_ROOT / "app/ai/agentic/skills_loader/loader.py")
    assert file_filter(BACKEND_ROOT / "config/reload-trigger.json")

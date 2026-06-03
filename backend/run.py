from pathlib import Path

import uvicorn
from app.core.config import settings

BACKEND_ROOT = Path(__file__).resolve().parent

RELOAD_EXCLUDES = [
    str(BACKEND_ROOT / "app/ai/agentic/tooling/news_plugins/external"),
    str(BACKEND_ROOT / "app/ai/agentic/skills_loader/skills"),
    "app/ai/agentic/tooling/news_plugins/external",
    "app/ai/agentic/skills_loader/skills",
]

if __name__ == "__main__":
    uvicorn.run(
        "app.main:create_app",
        host="0.0.0.0",
        port=8000,
        reload=settings.APP_RELOAD,
        factory=True,
        log_config='config/log_config.json',
        timeout_graceful_shutdown=0,
        reload_dirs=["app", "config"],
        reload_includes=["*.json"],
        reload_excludes=RELOAD_EXCLUDES,
    )

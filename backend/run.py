import uvicorn
from app.core.config import settings


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
    )

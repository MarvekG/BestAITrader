from __future__ import annotations

import os
from pathlib import Path

import uvicorn


WEBFETCH_ROOT = Path(__file__).resolve().parent


def _get_bool_env(name: str, default: bool = False) -> bool:
    """
    读取布尔环境变量。

    Args:
        name: 环境变量名称。
        default: 环境变量缺失时使用的默认值。

    Returns:
        解析后的布尔值。
    """
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def main() -> None:
    """启动 Webfetch 抓取容器 HTTP 服务。"""
    reload_enabled = _get_bool_env("WEBFETCH_RELOAD", default=False)
    Path(os.getenv("WEBFETCH_RUNTIME_DIR", "/runtime"), "logs").mkdir(parents=True, exist_ok=True)
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8010,
        reload=reload_enabled,
        reload_dirs=["/app"] if reload_enabled else None,
        log_config=str(WEBFETCH_ROOT / "config/log_config.json"),
    )


if __name__ == "__main__":
    main()

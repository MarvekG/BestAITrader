from functools import lru_cache

from pydantic import ConfigDict, Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Webfetch 抓取容器运行配置。"""

    WEBFETCH_MAX_PAGES: int = Field(default=4, ge=1)
    WEBFETCH_ENGINE_ACQUIRE_TIMEOUT_MS: int = Field(default=30_000, ge=1)
    WEBFETCH_DEFAULT_TIMEOUT_MS: int = Field(default=60_000, ge=1_000)
    WEBFETCH_DEFAULT_DOWNLOAD_TIMEOUT_SECONDS: float = Field(default=60.0, ge=1.0)
    WEBFETCH_DEFAULT_WAIT_AFTER_MS: int = Field(default=5_000, ge=0)
    WEBFETCH_PATCHRIGHT_HEADLESS: bool = True
    WEBFETCH_CAMOUFOX_HEADLESS: bool = True

    model_config = ConfigDict(case_sensitive=True)


@lru_cache
def get_settings() -> Settings:
    """
    获取进程级配置单例。

    Returns:
        Webfetch 抓取容器运行配置。
    """
    return Settings()

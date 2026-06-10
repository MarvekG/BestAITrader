from functools import lru_cache
from pathlib import Path

from pydantic import ConfigDict, Field
from pydantic_settings import BaseSettings


SANDBOX_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    """Python 沙箱容器运行配置。"""

    SANDBOX_ENABLED: bool = True
    SANDBOX_EXECUTION_MODE: str = Field(default="pooled_worker", pattern="^(pooled_worker|one_shot_worker|subprocess)$")
    SANDBOX_DENO_EXECUTABLE: str = "deno"
    SANDBOX_RUNNER_PATH: str = str(SANDBOX_ROOT / "app/workers/pyodide_runner.ts")
    SANDBOX_WORKER_RUNNER_PATH: str = str(SANDBOX_ROOT / "app/workers/pyodide_one_shot_worker.ts")
    SANDBOX_POOLED_WORKER_RUNNER_PATH: str = str(SANDBOX_ROOT / "app/workers/pyodide_pooled_worker.ts")
    SANDBOX_PYODIDE_ROOT: str = str(Path.home() / "pyodide")
    SANDBOX_TIMEOUT_SECONDS: int = Field(default=30, ge=1)
    SANDBOX_MAX_TIMEOUT_SECONDS: int = Field(default=60, ge=1)
    SANDBOX_STDOUT_MAX_BYTES: int = Field(default=32768, ge=1)
    SANDBOX_STDERR_MAX_BYTES: int = Field(default=16384, ge=1)
    SANDBOX_MAX_CONCURRENT_EXECUTIONS: int = Field(default=4, ge=1)
    SANDBOX_WORKER_POOL_SIZE: int = Field(default=4, ge=1)
    SANDBOX_WORKER_POOL_MAX_STARTING: int = Field(default=2, ge=1)
    SANDBOX_PREWARM_POOL_ENABLED: bool = True
    SANDBOX_PREWARM_ON_STARTUP: bool = True
    SANDBOX_STARTUP_PREWARM_WORKERS: int = Field(default=1, ge=1)
    SANDBOX_PREWARM_POOL_SIZE: int = Field(default=4, ge=1)
    SANDBOX_PREWARM_MAX_STARTING: int = Field(default=2, ge=1)
    SANDBOX_WORKER_ACQUIRE_TIMEOUT_SECONDS: int = Field(default=30, ge=1)
    SANDBOX_WORKER_STARTUP_TIMEOUT_SECONDS: int = Field(default=30, ge=1)

    model_config = ConfigDict(case_sensitive=True)


@lru_cache
def get_settings() -> Settings:
    """
    获取进程级配置单例。

    Returns:
        Python 沙箱容器运行配置。
    """
    return Settings()

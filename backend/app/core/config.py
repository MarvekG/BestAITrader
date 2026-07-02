from pathlib import Path
from typing import List, Literal

from pydantic import ConfigDict, field_validator
from pydantic_settings import BaseSettings

PROJECT_ROOT = Path(__file__).resolve().parents[2].absolute()


class Settings(BaseSettings):
    # Project Basic Info
    PROJECT_NAME: str = "天枢智投"
    PROJECT_VERSION: str = "v1.0.0"
    API_V1_STR: str = "/api/v1"

    # Security Config
    # 默认空字符串：启动时由 _require_secret_key 校验非空。
    SECRET_KEY: str = ""
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    APP_RELOAD: bool = True

    # Initial Superuser
    # FIRST_SUPERUSER_PASSWORD 默认空字符串：启动时由 _require_superuser_password 校验非空。
    FIRST_SUPERUSER: str = "tradeuser"
    FIRST_SUPERUSER_EMAIL: str = "tradeuser@example.com"
    FIRST_SUPERUSER_PASSWORD: str = ""

    # Database Config
    DATABASE_URL: str = "postgresql://tradeuser:tradepassword@postgres:5432/trading"
    ASYNC_DATABASE_URL: str = "postgresql+asyncpg://tradeuser:tradepassword@postgres:5432/trading"

    # Redis Config
    REDIS_URL: str = "redis://redis:6379"

    # LiteLLM Gateway Config
    LLM_PROVIDER: str = "litellm"
    LLM_BASE_URL: str = "http://litellm:4000/v1"
    LLM_API_KEY: str = "sk-litellm-gateway-key"
    LLM_MODEL: str = "openai-compatible"
    LLM_THINKING_MODEL: str = "openai-compatible-thinking"
    LLM_TIMEOUT_SECONDS: float = 240.0
    LLM_MAX_RETRIES: int = 3
    DEBATE_AGENT_PARALLEL_ENABLED: bool = True
    DEBATE_AGENT_MIN_ITERATIONS: int = 5
    ENABLE_AUTO_TRADE: bool = True
    ASYNC_TASK_MAX_CONCURRENT: int = 8
    PY_SANDBOX_ENABLED: bool = True
    PY_SANDBOX_BASE_URL: str = "http://sandbox:8030"
    PY_SANDBOX_HTTP_TIMEOUT_SECONDS: float = 35.0
    PY_SANDBOX_EXECUTION_MODE: Literal["pooled_worker", "one_shot_worker", "subprocess"] = "pooled_worker"
    PY_SANDBOX_TIMEOUT_SECONDS: int = 30
    PY_SANDBOX_STDOUT_MAX_BYTES: int = 32768
    PY_SANDBOX_STDERR_MAX_BYTES: int = 16384
    AGENTIC_DEPENDENCY_INSTALL_TIMEOUT_SECONDS: int = 600
    AGENTIC_DEPENDENCY_INSTALL_MAX_REQUIREMENTS: int = 50
    ENABLE_RUNTIME_EXTENSIONS: bool = True
    ENABLE_OPENAPI_DOCS: bool = True
    INTERACTIVE_RESEARCH_PLAN_MAX_ITERATIONS: int = 5
    BACKEND_CORS_ORIGINS: List[str] = []

    @field_validator("SECRET_KEY")
    @classmethod
    def _require_secret_key(cls, value: str) -> str:
        if not value:
            raise ValueError(
                "SECRET_KEY must be set in backend/.env. "
                "Generate one with: "
                "python -c 'import secrets; print(secrets.token_urlsafe(48))'"
            )
        return value

    @field_validator("FIRST_SUPERUSER_PASSWORD")
    @classmethod
    def _require_superuser_password(cls, value: str) -> str:
        if not value:
            raise ValueError(
                "FIRST_SUPERUSER_PASSWORD must be set in backend/.env. "
                "Use a unique value (recommend 12+ characters)."
            )
        return value

    @field_validator("MARKET_WATCH_EVENT_RETENTION_DAYS")
    @classmethod
    def _validate_market_watch_event_retention_days(cls, value: int) -> int:
        """
        校验盯盘事件保留天数，避免误配置导致过度删除或无限膨胀。

        Args:
            value: 环境变量或默认配置解析后的保留天数。

        Returns:
            校验通过的保留天数。

        Raises:
            ValueError: 保留天数不在允许范围内。
        """
        if value < 1 or value > 365:
            raise ValueError("MARKET_WATCH_EVENT_RETENTION_DAYS must be between 1 and 365")
        return value

    MARKET_WATCH_RECENT_DEBATE_LAUNCH_LOOKBACK_HOURS: int = 24
    MARKET_WATCH_EVENT_RETENTION_DAYS: int = 30

    # Core Indices Config
    CORE_INDICES: List[str] = [
        "000300.SH",  # 沪深300: 核心资产, 沪深两市市值最大、流动性最好的300只。 / CSI 300: Core assets, top 300 large cap and high liquidity.
        "000016.SH",  # 上证50: 蓝筹/大盘, 仅限上交所市值最大、流动性最好的50只。 / SSE 50: Blue chips, top 50 strictly in SSE.
        "399006.SZ",  # 创业板指: 科技/创新, 深交所创业板核心股，波动大，成长性强。 / ChiNext: Tech/Innovation, high growth and volatility.
        "000688.SH",  # 科创50: 硬科技, 仅限科创板公司，半导体、生物医药权重高。 / STAR 50: Hard tech, heavy in semi & biotech.
    ]

    # Data Source Config
    TUSHARE_MAX_CALLS_PER_MINUTE: int = 120
    DATA_SOURCE_RATE_LIMIT_TIMEOUT_SECONDS: float = 60.0
    AKSHARE_MAX_CALLS_PER_MINUTE: int = 60
    DEFAULT_DATA_SOURCE: str = "tushare"
    DEFAULT_HTTP_TIMEOUT: int = 120
    ENABLE_DATA_SOURCE_FAILOVER: bool = True

    # Memory Service Config
    MEMORY_SERVICE_ENABLED: bool = True
    MEMORY_SERVICE_BASE_URL: str = "http://memo:8020"
    MEMORY_SERVICE_TIMEOUT_SECONDS: float = 90.0

    # Webfetch Service Config
    WEBFETCH_BASE_URL: str = "http://webfetch:8010"
    WEBFETCH_TIMEOUT_SECONDS: float = 180.0

    # Experience Cleanup Config
    EXPERIENCE_CLEANUP_ENABLED: bool = True
    EXPERIENCE_INDEX_RETENTION_DAYS: int = 7
    EXPERIENCE_REVIEW_EVENT_RETENTION_DAYS: int = 30
    EXPERIENCE_CLEANUP_SCHEDULE_HOUR: int = 3
    EXPERIENCE_CLEANUP_SCHEDULE_MINUTE: int = 30
    ASYNC_TASK_CLEANUP_ENABLED: bool = True
    ASYNC_TASK_RETENTION_DAYS: int = 30
    ASYNC_TASK_CLEANUP_SCHEDULE_HOUR: int = 4
    ASYNC_TASK_CLEANUP_SCHEDULE_MINUTE: int = 0

    # System Language (zh/en)
    SYSTEM_LANGUAGE: str = "zh"

    model_config = ConfigDict(
        case_sensitive=True,
        extra="ignore",
        env_file=[
            str(PROJECT_ROOT / ".env"),
        ]
    )


settings = Settings()

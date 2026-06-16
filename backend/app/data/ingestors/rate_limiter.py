"""
通用限流器基类，基于漏桶算法。

用于数据源插件的 API 限流控制。
"""
import asyncio
import time
from typing import Optional

from app.core.logger import get_logger

logger = get_logger(__name__)


class LeakyBucketRateLimiter:
    """
    漏桶限流器（严格速率控制）。

    特点：
    - 初始令牌数为 0（无突发能力）
    - 每秒固定补充令牌，未使用的令牌立即丢弃（令牌上限为 1.0）
    - 严格控制请求速率为固定值
    """

    def __init__(self, max_calls_per_minute: int = 60):
        """
        初始化限流器。

        Args:
            max_calls_per_minute: 每分钟最大调用次数，默认 60。
        """
        self.max_calls_per_minute = max_calls_per_minute
        self.tokens = 0.0  # 漏桶算法：初始令牌数为 0
        self.last_update = time.monotonic()
        self.lock = asyncio.Lock()

        logger.info(
            "Rate limiter initialized (Leaky Bucket)",
            extra={
                "max_calls_per_minute": max_calls_per_minute,
                "algorithm": "leaky_bucket"
            }
        )

    async def acquire(self, timeout: Optional[float] = None) -> bool:
        """
        获取一个令牌（异步阻塞直到有令牌可用）。

        Args:
            timeout: 超时时间（秒），None 表示无限等待。

        Returns:
            获取成功返回 True；超时返回 False。
        """
        start_time = time.monotonic()

        while True:
            async with self.lock:
                now = time.monotonic()
                elapsed = now - self.last_update

                # 补充令牌（按每分钟补充速率）
                tokens_to_add = elapsed * (self.max_calls_per_minute / 60.0)
                # 漏桶算法：令牌上限为 1.0，多余令牌丢弃
                self.tokens = min(1.0, self.tokens + tokens_to_add)
                self.last_update = now

                # 如果有令牌，立即消耗并返回
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return True

                # 计算需要等待的时间
                tokens_needed = 1.0 - self.tokens
                wait_time = tokens_needed / (self.max_calls_per_minute / 60.0)

            # 检查超时
            if timeout is not None:
                elapsed_total = time.monotonic() - start_time
                if elapsed_total + wait_time > timeout:
                    return False

            # 等待令牌补充
            await asyncio.sleep(wait_time)

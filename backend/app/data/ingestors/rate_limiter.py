import asyncio
import time
from typing import Dict, Optional
from app.core.logger import get_logger

logger = get_logger(__name__)


class TushareRateLimiter:
    """
    Tushare Pro API 全局限流器，基于令牌桶算法。

    根据用户积分等级控制每分钟 API 调用频率：
    - 120分：50次/分钟
    - 2000分：200次/分钟
    - 5000分及以上：500次/分钟

    官方文档：https://tushare.pro/document/1?doc_id=290
    """

    # 积分等级对应的每分钟调用次数限制
    RATE_LIMITS: Dict[int, int] = {
        120: 50,
        2000: 200,
        5000: 500,
        10000: 500,
        15000: 500,
    }

    def __init__(self, credits: int = 5000):
        """
        初始化限流器。

        Args:
            credits: Tushare 用户积分等级，默认 5000。
        """
        self.credits = credits
        self.max_calls_per_minute = self._get_rate_limit(credits)
        self.tokens = float(self.max_calls_per_minute)
        self.last_update = time.monotonic()
        self.lock = asyncio.Lock()

        logger.info(
            "TushareRateLimiter initialized",
            extra={
                "credits": credits,
                "max_calls_per_minute": self.max_calls_per_minute,
            }
        )

    def _get_rate_limit(self, credits: int) -> int:
        """
        根据积分等级获取每分钟调用次数限制。

        Args:
            credits: 用户积分。

        Returns:
            每分钟最大调用次数。
        """
        # 从高到低查找匹配的积分等级
        for threshold in sorted(self.RATE_LIMITS.keys(), reverse=True):
            if credits >= threshold:
                return self.RATE_LIMITS[threshold]
        # 低于最低等级，返回最低限制
        return self.RATE_LIMITS[120]

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
                self.tokens = min(self.max_calls_per_minute, self.tokens + tokens_to_add)
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

    def try_acquire(self) -> bool:
        """
        尝试非阻塞地获取一个令牌（同步方法）。

        Returns:
            成功获取返回 True；无令牌可用返回 False。
        """
        now = time.monotonic()
        elapsed = now - self.last_update

        # 补充令牌
        tokens_to_add = elapsed * (self.max_calls_per_minute / 60.0)
        self.tokens = min(self.max_calls_per_minute, self.tokens + tokens_to_add)
        self.last_update = now

        # 尝试消耗令牌
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False

    def get_wait_time(self) -> float:
        """
        获取下一个令牌可用需要等待的时间（秒）。

        Returns:
            等待时间（秒），0 表示立即可用。
        """
        now = time.monotonic()
        elapsed = now - self.last_update

        # 补充令牌
        tokens_to_add = elapsed * (self.max_calls_per_minute / 60.0)
        current_tokens = min(self.max_calls_per_minute, self.tokens + tokens_to_add)

        if current_tokens >= 1.0:
            return 0.0

        # 计算等待时间
        tokens_needed = 1.0 - current_tokens
        return tokens_needed / (self.max_calls_per_minute / 60.0)

    def reset(self):
        """重置令牌桶（测试用）。"""
        self.tokens = float(self.max_calls_per_minute)
        self.last_update = time.monotonic()


# 全局单例
_global_limiter: Optional[TushareRateLimiter] = None


def get_tushare_rate_limiter(credits: Optional[int] = None) -> TushareRateLimiter:
    """
    获取全局 Tushare 限流器单例。

    Args:
        credits: Tushare 积分等级，仅在首次初始化时有效。

    Returns:
        全局限流器实例。
    """
    global _global_limiter
    if _global_limiter is None:
        from app.core.config import settings
        actual_credits = credits if credits is not None else settings.TUSHARE_CREDITS
        _global_limiter = TushareRateLimiter(credits=actual_credits)
    return _global_limiter

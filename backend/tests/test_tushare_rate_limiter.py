import asyncio
import time
import pytest
from app.data.ingestors.rate_limiter import TushareRateLimiter


class TestTushareRateLimiter:
    """测试 Tushare 限流器"""

    def test_rate_limit_mapping(self):
        """测试积分等级与限流对应关系"""
        # 120分 -> 50次/分钟
        limiter = TushareRateLimiter(credits=120)
        assert limiter.max_calls_per_minute == 50

        # 2000分 -> 200次/分钟
        limiter = TushareRateLimiter(credits=2000)
        assert limiter.max_calls_per_minute == 200

        # 5000分 -> 500次/分钟
        limiter = TushareRateLimiter(credits=5000)
        assert limiter.max_calls_per_minute == 500

        # 10000分 -> 500次/分钟
        limiter = TushareRateLimiter(credits=10000)
        assert limiter.max_calls_per_minute == 500

        # 低于最低等级 -> 50次/分钟
        limiter = TushareRateLimiter(credits=100)
        assert limiter.max_calls_per_minute == 50

    @pytest.mark.asyncio
    async def test_acquire_tokens(self):
        """测试令牌获取"""
        limiter = TushareRateLimiter(credits=120)  # 50次/分钟
        limiter.reset()

        # 初始应该有 50 个令牌
        assert limiter.tokens == 50.0

        # 连续获取 5 个令牌
        for _ in range(5):
            acquired = await limiter.acquire(timeout=1.0)
            assert acquired is True

        # 剩余约 45 个令牌（允许浮点误差）
        assert 44.9 <= limiter.tokens <= 45.1

    @pytest.mark.asyncio
    async def test_token_refill(self):
        """测试令牌补充"""
        limiter = TushareRateLimiter(credits=120)  # 50次/分钟 = 0.833次/秒
        limiter.reset()

        # 消耗所有令牌
        for _ in range(50):
            await limiter.acquire(timeout=1.0)

        assert limiter.tokens < 1.0

        # 等待 2 秒，应该补充约 1.67 个令牌
        await asyncio.sleep(2.0)

        # 应该能再获取至少 1 个令牌
        acquired = await limiter.acquire(timeout=0.1)
        assert acquired is True

    @pytest.mark.asyncio
    async def test_acquire_timeout(self):
        """测试超时机制"""
        limiter = TushareRateLimiter(credits=120)  # 50次/分钟
        limiter.tokens = 0.0  # 清空令牌

        # 设置很短的超时，应该失败
        start = time.monotonic()
        acquired = await limiter.acquire(timeout=0.1)
        elapsed = time.monotonic() - start

        assert acquired is False
        assert elapsed < 0.2  # 应该在超时时间内返回

    def test_try_acquire_non_blocking(self):
        """测试非阻塞获取"""
        limiter = TushareRateLimiter(credits=120)
        limiter.reset()

        # 有令牌时应该成功
        assert limiter.try_acquire() is True

        # 清空令牌后应该失败
        limiter.tokens = 0.0
        assert limiter.try_acquire() is False

    def test_get_wait_time(self):
        """测试等待时间计算"""
        limiter = TushareRateLimiter(credits=120)  # 50次/分钟
        limiter.reset()

        # 有足够令牌时，等待时间为 0
        assert limiter.get_wait_time() == 0.0

        # 清空令牌后，需要等待
        limiter.tokens = 0.0
        wait_time = limiter.get_wait_time()

        # 50次/分钟 = 1次/1.2秒，需要等待约 1.2 秒
        assert 1.0 < wait_time < 1.5

    @pytest.mark.asyncio
    async def test_concurrent_acquire(self):
        """测试并发获取令牌"""
        limiter = TushareRateLimiter(credits=2000)  # 200次/分钟
        limiter.reset()

        # 模拟 10 个并发请求
        tasks = [limiter.acquire(timeout=5.0) for _ in range(10)]
        results = await asyncio.gather(*tasks)

        # 所有请求都应该成功
        assert all(results)

        # 令牌应该减少约 10 个（允许浮点误差）
        assert 189.9 <= limiter.tokens <= 190.1

    @pytest.mark.asyncio
    async def test_rate_limiting_in_practice(self):
        """实际场景测试：模拟高频调用"""
        limiter = TushareRateLimiter(credits=120)  # 50次/分钟
        limiter.tokens = 3.0  # 只给 3 个令牌

        start = time.monotonic()

        # 尝试获取 5 个令牌
        for _ in range(5):
            await limiter.acquire(timeout=10.0)

        elapsed = time.monotonic() - start

        # 前 3 个立即获得，后 2 个需要等待补充
        # 50次/分钟 = 0.833次/秒，2个令牌需要约 2.4 秒
        assert elapsed > 2.0  # 至少等待 2 秒
        assert elapsed < 4.0  # 不应该等太久


def test_get_global_limiter():
    """测试全局单例"""
    from app.data.ingestors.rate_limiter import get_tushare_rate_limiter

    # 多次调用应该返回同一个实例
    limiter1 = get_tushare_rate_limiter()
    limiter2 = get_tushare_rate_limiter()

    assert limiter1 is limiter2

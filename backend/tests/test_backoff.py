import pytest

from app.core.utils.backoff import backoff


TUSHARE_RATE_LIMIT_MESSAGE = "您的请求频率过高，，请稍等后延迟0.5-1秒!"


@pytest.mark.asyncio
async def test_async_backoff_retries_tushare_rate_limit_when_retry_on_is_configured():
    calls = 0

    @backoff(max_tries=2, base_delay=0.0, max_delay=0.0, retry_on=(TimeoutError,))
    async def unstable_call():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise Exception(TUSHARE_RATE_LIMIT_MESSAGE)
        return "ok"

    assert await unstable_call() == "ok"
    assert calls == 2


def test_sync_backoff_retries_tushare_rate_limit_when_retry_on_is_configured():
    calls = 0

    @backoff(max_tries=2, base_delay=0.0, max_delay=0.0, retry_on=(TimeoutError,))
    def unstable_call():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise Exception(TUSHARE_RATE_LIMIT_MESSAGE)
        return "ok"

    assert unstable_call() == "ok"
    assert calls == 2

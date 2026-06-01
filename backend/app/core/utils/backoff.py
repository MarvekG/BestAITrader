import asyncio
import time
import logging
from functools import wraps
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


_RATE_LIMIT_KEYWORDS = (
    "too many request",
    "too many requests",
    "rate limit",
    "rate limited",
    "ratelimit",
    "请求频率过高",
    "访问频率",
    "访问频次",
    "频率过高",
    "请求过于频繁",
    "每分钟最多访问",
    "每分钟只能访问",
)

_NETWORK_RETRY_KEYWORDS = (
    "remotedisconnected",
    "connection aborted",
    "connection closed",
    "incomplete read",
)


def _is_rate_limit_error(error: Exception) -> bool:
    """
    判断异常是否为上游限频错误

    Args:
        error: 捕获到的异常

    Returns:
        是否为限频错误
    """
    message = str(error).lower()
    type_name = type(error).__name__
    return "YFRateLimitError" in type_name or any(keyword in message for keyword in _RATE_LIMIT_KEYWORDS)


def _should_retry(error: Exception, retry_on: Optional[tuple]) -> bool:
    """
    判断异常是否应该重试

    Args:
        error: 捕获到的异常
        retry_on: 触发重试的异常类型元组

    Returns:
        是否应该重试
    """
    if retry_on and isinstance(error, retry_on):
        return True

    message = str(error).lower()
    return _is_rate_limit_error(error) or any(keyword in message for keyword in _NETWORK_RETRY_KEYWORDS)


def backoff(max_tries: int = 3, base_delay: float = 1.0, max_delay: float = 10.0,
            backoff_type: str = 'exponential', increment: float = 5.0, retry_on: Optional[tuple] = None):
    """
    退避重试装饰器
    
    Args:
        max_tries: 最大重试次数
        base_delay: 初始延迟（秒）
        max_delay: 最大延迟（秒）
        backoff_type: 退避类型，可选值：'exponential'（指数退避）或 'linear'（线性退避）
        increment: 线性退避时的每次增加延迟（秒）
        retry_on: 触发重试的异常类型元组，默认为None（仅针对Rate Limit相关错误）
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args, **kwargs) -> Any:
            delay = base_delay
            for attempt in range(max_tries):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    should_retry = _should_retry(e, retry_on)
                    if should_retry:
                        if attempt < max_tries - 1:
                            logger.warning(
                                f"Operation failed, attempt {attempt + 1}/{max_tries} failed, retrying in {delay}s: {e}",
                                exc_info=False
                            )
                            await asyncio.sleep(delay)
                            # 根据退避类型更新延迟
                            if backoff_type == 'linear':
                                delay = min(delay + increment, max_delay)
                            else:
                                delay = min(delay * 2, max_delay)
                        else:
                            logger.error(
                                f"Operation failed, attempt {attempt + 1}/{max_tries} failed: {e}",
                                exc_info=True
                            )
                            raise
                    else:
                        raise

        @wraps(func)
        def sync_wrapper(*args, **kwargs) -> Any:
            delay = base_delay
            for attempt in range(max_tries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    should_retry = _should_retry(e, retry_on)
                    if should_retry:
                        if attempt < max_tries - 1:
                            logger.warning(
                                f"Operation failed, attempt {attempt + 1}/{max_tries} failed, retrying in {delay}s: {e}",
                                exc_info=False
                            )
                            time.sleep(delay)
                            # 根据退避类型更新延迟
                            if backoff_type == 'linear':
                                delay = min(delay + increment, max_delay)
                            else:
                                delay = min(delay * 2, max_delay)
                        else:
                            logger.error(
                                f"Operation failed, attempt {attempt + 1}/{max_tries} failed: {e}",
                                exc_info=True
                            )
                            raise
                    else:
                        raise
        
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator

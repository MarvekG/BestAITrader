from __future__ import annotations

import asyncio
from types import TracebackType


class EngineLimiter:
    """浏览器引擎并发页面限流器。"""

    def __init__(self, max_pages: int, acquire_timeout_ms: int = 30_000) -> None:
        """
        初始化限流器。

        Args:
            max_pages: 允许同时打开的最大页面数。
            acquire_timeout_ms: 等待页面执行额度的最长时间。
        """
        self._page_slots = asyncio.BoundedSemaphore(max(1, max_pages))
        self._acquire_timeout_ms = max(1, acquire_timeout_ms)

    def acquire(self) -> "EngineLimiterSlot":
        """
        获取一次页面执行额度。

        Returns:
            页面执行额度上下文。
        """
        return EngineLimiterSlot(self._page_slots, self._acquire_timeout_ms)


class EngineLimiterSlot:
    """浏览器引擎并发额度上下文。"""

    def __init__(self, page_slots: asyncio.BoundedSemaphore, acquire_timeout_ms: int) -> None:
        """
        初始化页面执行额度上下文。

        Args:
            page_slots: 共享页面并发信号量。
            acquire_timeout_ms: 等待页面执行额度的最长时间。
        """
        self._page_slots = page_slots
        self._acquire_timeout_ms = acquire_timeout_ms

    async def __aenter__(self) -> None:
        """
        等待并获取页面执行额度。

        Raises:
            EngineLimiterTimeoutError: 等待页面执行额度超时。
        """
        try:
            await asyncio.wait_for(self._page_slots.acquire(), timeout=self._acquire_timeout_ms / 1000)
        except asyncio.TimeoutError as exc:
            raise EngineLimiterTimeoutError(
                f"engine concurrency slot wait timed out after {self._acquire_timeout_ms}ms"
            ) from exc

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        """
        释放页面执行额度。

        Args:
            exc_type: 上下文内异常类型。
            exc: 上下文内异常实例。
            traceback: 上下文内异常堆栈。
        """
        self._page_slots.release()
        return False


class EngineLimiterTimeoutError(RuntimeError):
    """等待浏览器引擎并发额度超时。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator


class EngineLimiter:
    """浏览器引擎并发页面限流器。"""

    def __init__(self, max_pages: int) -> None:
        """
        初始化限流器。

        Args:
            max_pages: 允许同时打开的最大页面数。
        """
        self._page_slots = asyncio.BoundedSemaphore(max(1, max_pages))

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[None]:
        """
        获取一次页面执行额度。

        Yields:
            页面执行额度上下文。
        """
        async with self._page_slots:
            yield

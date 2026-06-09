from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True)
class RenderedPage:
    """浏览器渲染后的页面结果。"""

    final_url: str
    status: int | None
    title: str
    html: str
    selected_element_count: int | None


class BrowserEngine(Protocol):
    """网页渲染引擎协议。"""

    async def render(
        self,
        url: str,
        selectors: list[str],
        timeout_ms: int,
        wait_after_ms: int,
    ) -> RenderedPage:
        """
        渲染指定 URL 并返回页面内容。

        Args:
            url: 已规范化的目标 URL。
            selectors: CSS selector 列表。
            timeout_ms: 页面导航超时时间。
            wait_after_ms: 导航完成后的额外等待时间。

        Returns:
            浏览器渲染结果。
        """

    async def close(self) -> None:
        """关闭引擎持有的浏览器资源。"""

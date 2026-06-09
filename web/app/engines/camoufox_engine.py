from __future__ import annotations

from typing import Any

from app.engines.base import RenderedPage
from app.services.limiter import EngineLimiter
from app.services.renderer import (
    DEFAULT_LOCALE,
    DEFAULT_TIMEZONE,
    DEFAULT_VIEWPORT,
    DEFAULT_WAIT_UNTIL,
    select_rendered_html,
)


class CamoufoxEngine:
    """使用 Camoufox 渲染网页的引擎。"""

    def __init__(self, limiter: EngineLimiter, headless: bool = True) -> None:
        """
        初始化 Camoufox 引擎。

        Args:
            limiter: 共享页面并发限流器。
            headless: 是否使用 headless 模式。
        """
        self._limiter = limiter
        self._headless = headless

    async def render(
        self,
        url: str,
        selectors: list[str],
        timeout_ms: int,
        wait_after_ms: int,
    ) -> RenderedPage:
        """
        使用 Camoufox 渲染网页。

        Args:
            url: 已规范化的目标 URL。
            selectors: CSS selector 列表。
            timeout_ms: 页面导航超时时间。
            wait_after_ms: 导航完成后的额外等待时间。

        Returns:
            浏览器渲染结果。

        Raises:
            RuntimeError: Camoufox 未安装或启动失败。
        """
        try:
            from camoufox.async_api import AsyncCamoufox
        except ImportError as exc:
            raise RuntimeError("Camoufox is not installed in the web container") from exc

        async with self._limiter.acquire():
            try:
                async with AsyncCamoufox(headless=self._headless) as browser:
                    context: Any | None = None
                    try:
                        context = await browser.new_context(
                            viewport=DEFAULT_VIEWPORT,
                            locale=DEFAULT_LOCALE,
                            timezone_id=DEFAULT_TIMEZONE,
                        )
                        page = await context.new_page()
                        response = await page.goto(url, wait_until=DEFAULT_WAIT_UNTIL, timeout=timeout_ms)
                        if wait_after_ms:
                            await page.wait_for_timeout(wait_after_ms)
                        title = await page.title()
                        html, selected_element_count = await select_rendered_html(page, selectors)
                        return RenderedPage(
                            final_url=page.url,
                            status=response.status if response else None,
                            title=title,
                            html=html,
                            selected_element_count=selected_element_count,
                        )
                    finally:
                        if context is not None:
                            await context.close()
            except Exception as exc:
                raise RuntimeError(f"Camoufox render failed: {exc}") from exc

    async def close(self) -> None:
        """关闭 Camoufox 引擎资源。"""

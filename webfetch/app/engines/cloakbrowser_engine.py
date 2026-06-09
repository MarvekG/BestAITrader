from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from app.engines.base import DownloadedPdf, RenderedPage, timeout_seconds_to_ms, validate_pdf_download
from app.services.limiter import EngineLimiter
from app.services.renderer import (
    DEFAULT_LOCALE,
    DEFAULT_TIMEZONE,
    DEFAULT_VIEWPORT,
    DEFAULT_WAIT_UNTIL,
    select_rendered_html,
)

logger = logging.getLogger(__name__)


class CloakBrowserEngine:
    """使用 CloakBrowser 渲染网页的引擎。"""

    def __init__(self, limiter: EngineLimiter) -> None:
        """
        初始化 CloakBrowser 引擎。

        Args:
            limiter: 共享页面并发限流器。
        """
        self._browser_context: Any | None = None
        self._browser_context_lock = asyncio.Lock()
        self._limiter = limiter
        self._active_pages: set[Any] = set()
        self._browser_context_closing_reason: str | None = None

    async def _launch_default_browser_context(self) -> Any:
        """
        启动默认 CloakBrowser context。

        Returns:
            CloakBrowser context。
        """
        from cloakbrowser import launch_context_async

        return await launch_context_async(
            headless=True,
            viewport=DEFAULT_VIEWPORT,
            locale=DEFAULT_LOCALE,
            timezone=DEFAULT_TIMEZONE,
        )

    async def _get_or_launch_browser_context_locked(self) -> Any:
        """
        在锁内获取或启动 CloakBrowser context。

        Returns:
            可复用的 CloakBrowser context。

        Raises:
            RuntimeError: context 正在关闭。
        """
        if self._browser_context_closing_reason:
            raise RuntimeError(f"CloakBrowser context is closing: {self._browser_context_closing_reason}")
        if self._browser_context is None:
            self._browser_context = await self._launch_default_browser_context()
        return self._browser_context

    async def _close_browser_page(self, page: Any, reason: str | None = None) -> None:
        """
        关闭浏览器页面。

        Args:
            page: 待关闭页面。
            reason: 可选关闭原因。
        """
        if reason is None:
            await page.close()
            return
        try:
            await page.close(reason=reason)
        except TypeError:
            await page.close()

    @asynccontextmanager
    async def _open_browser_page(self) -> AsyncIterator[Any]:
        """
        打开一个 CloakBrowser 页面并在退出时关闭。

        Yields:
            CloakBrowser 页面对象。
        """
        page = None
        async with self._limiter.acquire():
            async with self._browser_context_lock:
                context = await self._get_or_launch_browser_context_locked()
                page = await context.new_page()
                self._active_pages.add(page)
            try:
                yield page
            finally:
                if page is not None and page in self._active_pages:
                    self._active_pages.discard(page)
                    await self._close_browser_page(page, self._browser_context_closing_reason)

    async def _close_active_pages(self, reason: str) -> None:
        """
        关闭所有活跃页面。

        Args:
            reason: 页面关闭原因。
        """
        pages = list(self._active_pages)
        self._active_pages.clear()
        for page in pages:
            await self._close_browser_page(page, reason)

    async def render(
        self,
        url: str,
        selectors: list[str],
        timeout_ms: int,
        wait_after_ms: int,
    ) -> RenderedPage:
        """
        使用 CloakBrowser 渲染网页。

        Args:
            url: 已规范化的目标 URL。
            selectors: CSS selector 列表。
            timeout_ms: 页面导航超时时间。
            wait_after_ms: 导航完成后的额外等待时间。

        Returns:
            浏览器渲染结果。
        """
        async with self._open_browser_page() as page:
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

    async def download_pdf(self, url: str, timeout: float) -> DownloadedPdf:
        """
        使用 CloakBrowser request context 下载 PDF。

        Args:
            url: 已规范化的 PDF URL。
            timeout: 请求超时时间，单位秒。

        Returns:
            PDF 下载结果。

        Raises:
            RuntimeError: 下载失败、内容为空或内容不是有效 PDF。
        """
        async with self._limiter.acquire():
            async with self._browser_context_lock:
                context = await self._get_or_launch_browser_context_locked()
            response = await context.request.get(url, timeout=timeout_seconds_to_ms(timeout))

        status = response.status
        final_url = response.url
        headers = response.headers or {}
        content_type = str(headers.get("content-type") or headers.get("Content-Type") or "")
        pdf_content = await response.body()

        validate_pdf_download(status, content_type, pdf_content)
        if "pdf" not in content_type.lower():
            logger.warning(
                "downloaded content is not explicitly marked as PDF",
                extra={"url": final_url, "content_type": content_type},
            )

        return DownloadedPdf(
            final_url=final_url,
            status=status,
            content_type=content_type,
            content=pdf_content,
        )

    async def close(self, reason: str = "browser_context_closed") -> None:
        """
        关闭 CloakBrowser context 和活跃页面。

        Args:
            reason: 资源关闭原因。
        """
        async with self._browser_context_lock:
            context = self._browser_context
            self._browser_context = None
            self._browser_context_closing_reason = reason

        try:
            await self._close_active_pages(reason)
            if context is not None:
                await context.close()
        finally:
            async with self._browser_context_lock:
                if self._browser_context is None:
                    self._browser_context_closing_reason = None

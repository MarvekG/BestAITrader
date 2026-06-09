from __future__ import annotations

from typing import Any

import logging

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
            raise RuntimeError("Camoufox is not installed in the webfetch container") from exc

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

    async def download_pdf(self, url: str, timeout: float) -> DownloadedPdf:
        """
        使用 Camoufox request context 下载 PDF。

        Args:
            url: 已规范化的 PDF URL。
            timeout: 请求超时时间，单位秒。

        Returns:
            PDF 下载结果。

        Raises:
            RuntimeError: 下载失败、内容为空或内容不是有效 PDF。
        """
        try:
            from camoufox.async_api import AsyncCamoufox
        except ImportError as exc:
            raise RuntimeError("Camoufox is not installed in the webfetch container") from exc

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
                        response = await context.request.get(url, timeout=timeout_seconds_to_ms(timeout))
                        status = response.status
                        final_url = response.url
                        headers = response.headers or {}
                        content_type = str(headers.get("content-type") or headers.get("Content-Type") or "")
                        pdf_content = await response.body()
                    finally:
                        if context is not None:
                            await context.close()
            except Exception as exc:
                raise RuntimeError(f"Camoufox PDF download failed: {exc}") from exc

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

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


@dataclass(slots=True)
class DownloadedPdf:
    """浏览器下载后的 PDF 结果。"""

    final_url: str
    status: int | None
    content_type: str
    content: bytes


def validate_pdf_download(status: int | None, content_type: str, content: bytes) -> None:
    """
    校验下载结果是否为可用 PDF。

    Args:
        status: 源站 HTTP 状态码。
        content_type: 源站 Content-Type。
        content: 下载到的原始 bytes。

    Raises:
        RuntimeError: 下载失败、内容为空或内容不是有效 PDF。
    """
    if status and status >= 400:
        raise RuntimeError(f"PDF download failed with HTTP status {status}")
    if not content:
        raise RuntimeError("Downloaded PDF is empty")
    if not content.lstrip().startswith(b"%PDF"):
        raise RuntimeError(
            f"Downloaded content is not a valid PDF: content_type={content_type or 'unknown'} bytes={len(content)}"
        )


def timeout_seconds_to_ms(timeout: float) -> int:
    """
    将秒级超时转换为浏览器 API 需要的毫秒整数。

    Args:
        timeout: 秒级超时。

    Returns:
        毫秒级超时。
    """
    return max(1_000, int(timeout * 1_000))


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


class PdfDownloadEngine(BrowserEngine, Protocol):
    """支持 PDF 下载的浏览器引擎协议。"""

    async def download_pdf(self, url: str, timeout: float) -> DownloadedPdf:
        """
        下载 PDF 并返回原始内容。

        Args:
            url: 已规范化的 PDF URL。
            timeout: 下载超时时间，单位秒。

        Returns:
            PDF 下载结果。
        """

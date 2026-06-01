from __future__ import annotations

import asyncio
from typing import Any, Dict, Literal
from urllib.parse import urlparse, urlunparse

from langchain.tools import tool
from markdownify import markdownify as html_to_markdown

from app.ai.agentic.tooling import browser_context
from app.core.logger import get_logger

logger = get_logger(__name__)

DEFAULT_WAIT_UNTIL = "domcontentloaded"

ContentFormat = Literal["html", "markdown"]

_browser_context: Any | None = None
_browser_context_lock = asyncio.Lock()


def _normalize_browser_url(raw_url: str) -> str:
    stripped_url = raw_url.strip()
    if not stripped_url:
        raise ValueError("url is required")

    normalized_input = stripped_url if "://" in stripped_url else f"https://{stripped_url}"
    parsed = urlparse(normalized_input)

    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError("only http and https URLs are supported")
    if not parsed.netloc or not parsed.hostname:
        raise ValueError("url must include a hostname")

    return urlunparse((scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))


def _convert_html_to_markdown(html: str, title: str, source_url: str) -> str:
    markdown_body = html_to_markdown(html, heading_style="ATX").strip()
    heading = title.strip() or source_url
    return f"# {heading}\n\nSource URL: {source_url}\n\n{markdown_body}".strip()


def _normalize_content_selectors(content_selectors: list[str] | None) -> list[str]:
    if not content_selectors:
        return []
    return [str(selector).strip() for selector in content_selectors if str(selector).strip()]


async def _select_rendered_html(page: Any, content_selectors: list[str] | None) -> tuple[str, int | None]:
    selectors = _normalize_content_selectors(content_selectors)
    if not selectors:
        html = await page.content()
        return html, None

    result = await page.evaluate(
        """
        (selectors) => {
          const selected = [];
          const seen = new Set();
          for (const selector of selectors) {
            for (const element of document.querySelectorAll(selector)) {
              if (seen.has(element)) {
                continue;
              }
              seen.add(element);
              selected.push(element.outerHTML);
            }
          }
          return {
            html: selected.join("\\n"),
            selected_element_count: selected.length,
          };
        }
        """,
        selectors,
    )
    if not isinstance(result, dict):
        return "", 0
    return str(result.get("html") or ""), int(result.get("selected_element_count") or 0)


async def _get_browser_context() -> Any:
    global _browser_context

    async with _browser_context_lock:
        _browser_context = await browser_context.get_browser_context()
        return _browser_context


async def close_browser_context(reason: str = "browser_context_closed") -> None:
    """关闭复用的 CloakBrowser context。"""
    global _browser_context

    async with _browser_context_lock:
        _browser_context = None

    await browser_context.close_browser_context(reason=reason)


async def render_web_page_html(
    url: str,
    content_format: ContentFormat = "html",
    timeout_ms: int = 60_000,
    wait_after_ms: int = 10_000,
    content_selectors: list[str] | None = None,
) -> Dict[str, Any]:
    """
    使用 CloakBrowser 渲染网页，并返回浏览器当前 DOM HTML 或 Markdown。

    Args:
        url: 要浏览的网页 URL。
        content_format: 返回内容格式，支持 html 或 markdown。
        timeout_ms: 页面导航超时时间，单位毫秒。
        wait_after_ms: 导航完成后额外等待 JS 渲染的时间，单位毫秒。
        content_selectors: 可选 CSS selector 列表；为空时返回完整页面，非空时只返回匹配区域。

    Returns:
        包含状态码、最终 URL、标题和渲染后内容的字典。
    """
    try:
        normalized_url = _normalize_browser_url(url)
    except ValueError as exc:
        return {
            "url": url,
            "error": str(exc),
            "content_source": "rendered_dom_html",
        }

    try:
        await _get_browser_context()
        async with browser_context.open_browser_page() as page:
            response = await page.goto(normalized_url, wait_until=DEFAULT_WAIT_UNTIL, timeout=timeout_ms)
            if wait_after_ms:
                await page.wait_for_timeout(wait_after_ms)

            title = await page.title()
            html, selected_element_count = await _select_rendered_html(page, content_selectors)
            html_length = len(html)
            base_result = {
                "url": normalized_url,
                "final_url": page.url,
                "status": response.status if response else None,
                "title": title,
                "content_format": content_format,
                "content_selectors": _normalize_content_selectors(content_selectors),
                "selected_element_count": selected_element_count,
            }

            if content_format == "markdown":
                markdown = _convert_html_to_markdown(html, title, page.url)
                return {
                    **base_result,
                    "markdown": markdown,
                    "markdown_length": len(markdown),
                    "source_html_length": html_length,
                    "content_source": "rendered_dom_markdown",
                }

            return {
                **base_result,
                "html": html,
                "html_length": html_length,
                "content_source": "rendered_dom_html",
            }
    except Exception as exc:
        closing_reason = browser_context.get_browser_closing_reason()
        if closing_reason:
            logger.info(
                "render_web_page_html interrupted by browser context closing: url=%s reason=%s error=%s",
                normalized_url,
                closing_reason,
                exc,
            )
            return {
                "url": normalized_url,
                "error": f"Browser context is closing: {closing_reason}",
                "content_source": "rendered_dom_html",
            }

        logger.exception("render_web_page_html failed: url=%s error=%s", normalized_url, exc)
        return {
            "url": normalized_url,
            "error": f"{type(exc).__name__}: {exc}",
            "content_source": "rendered_dom_html",
        }


@tool(parse_docstring=True)
async def browse_web_page_html(
    url: str,
    content_format: ContentFormat = "html",
    timeout_ms: int = 60_000,
    wait_after_ms: int = 5_000,
    content_selectors: list[str] | None = None,
) -> Dict[str, Any]:
    """
    使用 CloakBrowser 打开网页，执行页面 JavaScript，并返回渲染后的 HTML 或 Markdown。

    Args:
        url: 要浏览的网页 URL；缺少协议时默认按 https:// 处理。
        content_format: 返回内容格式，默认html，可选markdown。html 返回浏览器当前 DOM HTML；markdown 将同一份渲染后 HTML 转成
            Markdown，并在 Markdown 开头写入 Source URL。
        timeout_ms: page.goto 的导航超时时间，单位毫秒。
        wait_after_ms: 导航完成后额外等待前端 JS 渲染的时间，单位毫秒。
        content_selectors: 可选 CSS selector 列表；为空时返回完整页面，非空时只返回匹配区域。

    Returns:
        包含状态码、最终 URL、标题和渲染后内容的字典。
    """
    return await render_web_page_html(
        url=url,
        content_format=content_format,
        timeout_ms=timeout_ms,
        wait_after_ms=wait_after_ms,
        content_selectors=content_selectors,
    )

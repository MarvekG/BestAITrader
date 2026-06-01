from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Protocol

from app.ai.market_watch.schemas import (
    MarketWatchMarkdownDocument,
    MarketWatchSourceType,
    clean_market_watch_markdown,
    parse_market_watch_source_config,
)
from app.core.logger import get_logger

logger = get_logger(__name__)


class BrowserMarkdownToolLike(Protocol):
    """Protocol for the browser tool used to render source pages."""

    async def ainvoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Render a web page and return a structured browser result."""


def _document_id(source_type: MarketWatchSourceType, index: int, url: str) -> str:
    digest = sha256(url.encode("utf-8")).hexdigest()[:16]
    return f"{source_type}:{index}:{digest}"


def _coerce_browser_result(result: Any, url: str) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {
            "url": url,
            "markdown": "",
            "error": f"Unexpected browser result type: {type(result).__name__}",
        }
    return result


async def _fetch_market_watch_document(
    *,
    index: int,
    source_config: str,
    source_type: MarketWatchSourceType,
    browser_tool: BrowserMarkdownToolLike,
    clean_markdown: bool,
    markdown_cleanup_patterns: list[str] | None,
) -> MarketWatchMarkdownDocument:
    captured_at = datetime.now(timezone.utc)
    parsed_source = parse_market_watch_source_config(source_config)
    try:
        raw_result = await browser_tool.ainvoke(
            {
                "url": parsed_source.url,
                "content_format": "markdown",
                "content_selectors": parsed_source.content_selectors,
            }
        )
        result = _coerce_browser_result(raw_result, parsed_source.url)
    except Exception as exc:
        logger.exception(
            "Market watch source render failed",
            extra={
                "source_type": source_type,
                "url": parsed_source.url,
                "error": str(exc),
            },
        )
        result = {
            "url": parsed_source.url,
            "markdown": "",
            "error": f"{type(exc).__name__}: {exc}",
        }

    markdown = result.get("markdown")
    if markdown is None:
        markdown = ""
    markdown_text = str(markdown)
    if clean_markdown:
        markdown_text = clean_market_watch_markdown(markdown_text, markdown_cleanup_patterns)

    return MarketWatchMarkdownDocument(
        id=_document_id(source_type, index, parsed_source.url),
        source_type=source_type,
        url=parsed_source.url,
        final_url=result.get("final_url") or result.get("url") or parsed_source.url,
        title=result.get("title"),
        markdown=markdown_text,
        status=result.get("status") if isinstance(result.get("status"), int) else None,
        error=str(result["error"]) if result.get("error") else None,
        captured_at=captured_at,
    )


async def fetch_market_watch_documents(
    urls: list[str],
    source_type: MarketWatchSourceType,
    *,
    browser_tool: BrowserMarkdownToolLike | None = None,
    clean_markdown: bool = True,
    markdown_cleanup_patterns: list[str] | None = None,
) -> list[MarketWatchMarkdownDocument]:
    """
    Render configured market-watch source pages as full Markdown documents.

    Args:
        urls: User-configured page URLs to render.
        source_type: Whether the URLs represent market data or news context.
        browser_tool: Optional browser tool override for tests.
        clean_markdown: Whether to apply configured cleanup to rendered Markdown.
        markdown_cleanup_patterns: Regex patterns to apply when cleanup is enabled.

    Returns:
        One Markdown document per configured URL, including error documents when rendering fails.
    """
    if not urls:
        return []

    if browser_tool is None:
        from app.ai.agentic.tooling.browser_tool import browse_web_page_html

        browser_tool = browse_web_page_html

    return await asyncio.gather(
        *(
            _fetch_market_watch_document(
                index=index,
                source_config=url,
                source_type=source_type,
                browser_tool=browser_tool,
                clean_markdown=clean_markdown,
                markdown_cleanup_patterns=markdown_cleanup_patterns,
            )
            for index, url in enumerate(urls)
        )
    )

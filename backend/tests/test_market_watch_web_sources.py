from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.ai.market_watch.web_sources import fetch_market_watch_documents


class UrlAwareBrowserMarkdownTool:
    """Test double that returns Markdown based on the requested URL."""

    async def ainvoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return URL-specific Markdown."""
        return {
            "url": payload["url"],
            "final_url": payload["url"],
            "status": 200,
            "title": payload["url"],
            "markdown": f"# Page\nREMOVE ME\nKeep {payload['url']}\n",
        }


class FakeBrowserMarkdownTool:
    """Test double that records browser tool calls and returns Markdown."""

    def __init__(self, markdown: str) -> None:
        self.markdown = markdown
        self.calls: list[dict[str, Any]] = []

    async def ainvoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return a rendered Markdown payload."""
        self.calls.append(payload)
        return {
            "url": payload["url"],
            "final_url": "https://example.com/rendered",
            "status": 200,
            "title": "Rendered Page",
            "markdown": self.markdown,
        }


class BlockingBrowserMarkdownTool:
    """Test double that blocks calls so tests can observe concurrency."""

    def __init__(self, expected_calls: int) -> None:
        self.expected_calls = expected_calls
        self.release = asyncio.Event()
        self.all_started = asyncio.Event()
        self.calls: list[dict[str, Any]] = []
        self.active_calls = 0
        self.max_active_calls = 0

    async def ainvoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Record the call and wait until the test releases all calls."""
        self.calls.append(payload)
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        if len(self.calls) == self.expected_calls:
            self.all_started.set()
        await self.release.wait()
        self.active_calls -= 1
        return {
            "url": payload["url"],
            "final_url": payload["url"],
            "status": 200,
            "title": payload["url"],
            "markdown": f"# {payload['url']}",
        }


@pytest.mark.asyncio
async def test_fetch_market_watch_documents_uses_browser_tool_markdown_without_truncating() -> None:
    markdown = "# Rendered Page\n\n" + ("full markdown body\n" * 400)
    browser_tool = FakeBrowserMarkdownTool(markdown)

    documents = await fetch_market_watch_documents(
        ["https://example.com/source"],
        "news",
        browser_tool=browser_tool,
    )

    assert browser_tool.calls == [
        {
            "url": "https://example.com/source",
            "content_format": "markdown",
            "content_selectors": [],
        }
    ]
    assert len(documents) == 1
    assert documents[0].source_type == "news"
    assert documents[0].url == "https://example.com/source"
    assert documents[0].final_url == "https://example.com/rendered"
    assert documents[0].title == "Rendered Page"
    assert documents[0].markdown == markdown
    assert documents[0].error is None


@pytest.mark.asyncio
async def test_fetch_market_watch_documents_binds_selectors_to_source_url() -> None:
    browser_tool = FakeBrowserMarkdownTool("# Selected")

    documents = await fetch_market_watch_documents(
        [
            "example.com/source @@ body > div.main @@ #news-list",
            "https://example.com/full",
        ],
        "data",
        browser_tool=browser_tool,
    )

    assert browser_tool.calls == [
        {
            "url": "https://example.com/source",
            "content_format": "markdown",
            "content_selectors": ["body > div.main", "#news-list"],
        },
        {
            "url": "https://example.com/full",
            "content_format": "markdown",
            "content_selectors": [],
        },
    ]
    assert [document.url for document in documents] == ["https://example.com/source", "https://example.com/full"]


@pytest.mark.asyncio
async def test_fetch_market_watch_documents_loads_urls_concurrently() -> None:
    urls = [
        "https://example.com/one",
        "https://example.com/two",
        "https://example.com/three",
    ]
    browser_tool = BlockingBrowserMarkdownTool(expected_calls=len(urls))
    fetch_task = asyncio.create_task(
        fetch_market_watch_documents(
            urls,
            "news",
            browser_tool=browser_tool,
        )
    )

    try:
        await asyncio.wait_for(browser_tool.all_started.wait(), timeout=1)
        assert browser_tool.max_active_calls == len(urls)
    finally:
        browser_tool.release.set()
        documents = await asyncio.wait_for(fetch_task, timeout=1)

    assert [document.url for document in documents] == urls


@pytest.mark.asyncio
async def test_fetch_market_watch_documents_returns_raw_markdown() -> None:
    markdown = "# Page\n![](/newstatic/images/app_qr.png)\nKeep me\n"
    browser_tool = FakeBrowserMarkdownTool(markdown)

    documents = await fetch_market_watch_documents(
        ["https://example.com/source"],
        "news",
        browser_tool=browser_tool,
    )

    assert "![](/newstatic/images/app_qr.png)" in documents[0].markdown
    assert "Keep me" in documents[0].markdown


@pytest.mark.asyncio
async def test_fetch_market_watch_documents_applies_cleanup_patterns_per_source() -> None:
    documents = await fetch_market_watch_documents(
        [
            {"url": "https://example.com/clean", "cleanup_patterns": [r"(?m)^REMOVE ME\n?"]},
            {"url": "https://example.com/raw", "cleanup_patterns": []},
        ],
        "news",
        browser_tool=UrlAwareBrowserMarkdownTool(),
    )

    assert "REMOVE ME" not in documents[0].markdown
    assert "Keep https://example.com/clean" in documents[0].markdown
    assert "REMOVE ME" in documents[1].markdown
    assert "Keep https://example.com/raw" in documents[1].markdown


@pytest.mark.asyncio
async def test_fetch_market_watch_documents_returns_error_document_when_browser_tool_fails() -> None:
    class RaisingBrowserTool:
        async def ainvoke(self, payload: dict[str, Any]) -> dict[str, Any]:
            """Raise a deterministic browser failure."""
            _ = payload
            raise RuntimeError("browser unavailable")

    documents = await fetch_market_watch_documents(
        ["https://example.com/source"],
        "data",
        browser_tool=RaisingBrowserTool(),
    )

    assert len(documents) == 1
    assert documents[0].source_type == "data"
    assert documents[0].url == "https://example.com/source"
    assert documents[0].markdown == ""
    assert documents[0].error == "RuntimeError: browser unavailable"

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.ai.market_watch.schemas import DEFAULT_MARKET_WATCH_MARKDOWN_CLEANUP_PATTERNS
from app.ai.market_watch.web_sources import fetch_market_watch_documents


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
async def test_fetch_market_watch_documents_cleans_empty_list_pipe_lines_by_default() -> None:
    link_only_navigation = (
        "[新股申购](http://js1.example.com/tg.aspx?ID=520)"
        "[新股日历](http://js1.example.com/tg.aspx?ID=522)"
        "[资金流向](http://js1.example.com/tg.aspx?ID=525)"
        "[AH股比价](//quote.example.com/center/gridlist.html#ah_comparison)"
    )
    javascript_link_only = "[自选](javascript:;)"
    bullet_link_only = "* [我的资产](https://trade.example.com/MyAssets/Default)"
    linked_images_only = (
        "[![](data:image/gif;base64,R0lGODlhTgBkAPf/AP///yw+UKyw)](#fullScreenChart)"
        "[![](/newstatic/images/ty.png)](//quote.example.com/unify/cr/1.601919?from=classic)"
    )
    standalone_data_image = "![](data:imagexxx)"
    escaped_data_image = r"\![](data:image/gif;base64,R0lGODlhTgBkAPf/AP///yw+UKyw)"
    truncated_data_image = "![](data:image/gif"
    split_data_image = "![]\n(data:image/gif;base64,R0lGODlhTgBkAPf)"
    normal_chart_image = "![Chart](https://img.example.com/chart.png)"
    relative_qr_image = "![](/newstatic/images/app_qr.png)"
    protocol_relative_notice_image = '![](//g1.dfcfw.com/g3/notice.png "notice\nline")'
    markdown = "\n".join(
        [
            "# Page",
            "Company filing: [filing](https://example.com/filing?token=abc)",
            (
                "Market index: [**SSE**](//quote.example.com/unify/r/1.000001): 4131.53 "
                "up: [**1008**](//quote.example.com/center/gridlist.html#board)"
            ),
            "[焦点](http://finance.example.com/yaowen.html)",
            javascript_link_only,
            bullet_link_only,
            link_only_navigation,
            normal_chart_image,
            f"QR image: {relative_qr_image}",
            f"Notice image: {protocol_relative_notice_image}",
            "<https://bare.example.com/path>",
            "Plain URL: https://plain.example.com/news?id=1",
            "Bracketed URL: [https://bracket.example.com/path]",
            "Loose URL targets: (//quote.example.com/unify/r/1.000001)",
            "(http://quote.example.com/center/)",
            "(https://quote.example.com/center/)",
            "|",
            "|     |",
            "| | |",
            "*",
            "* ",
            "* |",
            "*\u00a0|\u00a0",
            "* | |",
            "[]()[]()",
            "[](#fullScreenChart)[](//example.com/quote?from=classic)",
            linked_images_only,
            standalone_data_image,
            f"Escaped data image: {escaped_data_image}",
            f"Truncated data image: {truncated_data_image}",
            f"Split data image: {split_data_image}",
            "Useful text",
        ]
    )
    browser_tool = FakeBrowserMarkdownTool(markdown)

    documents = await fetch_market_watch_documents(
        ["https://example.com/source"],
        "news",
        browser_tool=browser_tool,
        markdown_cleanup_patterns=DEFAULT_MARKET_WATCH_MARKDOWN_CLEANUP_PATTERNS,
    )

    assert "Company filing: [filing]" in documents[0].markdown
    assert (
        "Market index: [**SSE**]: 4131.53 "
        "up: [**1008**]"
    ) in documents[0].markdown
    assert "[焦点](http://finance.example.com/yaowen.html)" not in documents[0].markdown
    assert javascript_link_only not in documents[0].markdown
    assert bullet_link_only not in documents[0].markdown
    assert link_only_navigation not in documents[0].markdown
    assert normal_chart_image not in documents[0].markdown
    assert "![Chart]" not in documents[0].markdown
    assert relative_qr_image not in documents[0].markdown
    assert protocol_relative_notice_image not in documents[0].markdown
    assert "QR image: " in documents[0].markdown
    assert "Notice image: " in documents[0].markdown
    assert "<https://bare.example.com/path>" in documents[0].markdown
    assert "Plain URL: https://plain.example.com/news?id=1" in documents[0].markdown
    assert "Bracketed URL: [https://bracket.example.com/path]" in documents[0].markdown
    assert "Loose URL targets: " in documents[0].markdown
    assert "(//quote.example.com/unify/r/1.000001)" not in documents[0].markdown
    assert "(http://quote.example.com/center/)" not in documents[0].markdown
    assert "(https://quote.example.com/center/)" not in documents[0].markdown
    assert "\n|\n" in documents[0].markdown
    assert "\n|     |\n" in documents[0].markdown
    assert "\n| | |\n" in documents[0].markdown
    cleaned_lines = [line.strip() for line in documents[0].markdown.splitlines()]
    assert "* |" not in cleaned_lines
    assert "*" in cleaned_lines
    assert "* | |" in cleaned_lines
    assert "[]()[]()" in documents[0].markdown
    assert "[](#fullScreenChart)[](//example.com/quote?from=classic)" not in documents[0].markdown
    assert linked_images_only not in documents[0].markdown
    assert standalone_data_image not in documents[0].markdown
    assert "data:image/gif;base64" not in documents[0].markdown
    assert "Escaped data image: " in documents[0].markdown
    assert truncated_data_image not in documents[0].markdown
    assert "Truncated data image: " in documents[0].markdown
    assert split_data_image not in documents[0].markdown
    assert "Split data image: " in documents[0].markdown
    assert "Useful text" in documents[0].markdown


@pytest.mark.asyncio
async def test_fetch_market_watch_documents_applies_configured_cleanup_patterns() -> None:
    markdown = "# Page\nREMOVE ME\nKeep me\n"
    browser_tool = FakeBrowserMarkdownTool(markdown)

    documents = await fetch_market_watch_documents(
        ["https://example.com/source"],
        "news",
        browser_tool=browser_tool,
        markdown_cleanup_patterns=[r"(?m)^REMOVE ME\n?"],
    )

    assert "REMOVE ME" not in documents[0].markdown
    assert "Keep me" in documents[0].markdown


@pytest.mark.asyncio
async def test_fetch_market_watch_documents_does_not_use_default_cleanup_without_config_patterns() -> None:
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
async def test_fetch_market_watch_documents_can_keep_markdown_urls_when_cleaning_disabled() -> None:
    markdown = "[Company filing](https://example.com/filing?token=abc)\n* |"
    browser_tool = FakeBrowserMarkdownTool(markdown)

    documents = await fetch_market_watch_documents(
        ["https://example.com/source"],
        "news",
        browser_tool=browser_tool,
        clean_markdown=False,
    )

    assert documents[0].markdown == markdown


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

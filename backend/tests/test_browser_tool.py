from unittest.mock import AsyncMock, patch

import pytest

from app.ai.agentic.tooling import browser_tool
from app.ai.agentic.tooling.browser_tool import browse_web_page_html, render_web_page_html


def _webfetch_response(
    content: str,
    return_type: str = "html",
    content_source: str | None = None,
    **overrides: object,
) -> dict[str, object]:
    """
    构造测试用 webfetch 响应。

    Args:
        content: 返回内容。
        return_type: 内容格式。
        content_source: 可选内容来源。
        **overrides: 覆盖字段。

    Returns:
        webfetch 响应字典。
    """
    payload: dict[str, object] = {
        "success": True,
        "url": "https://example.com",
        "final_url": "https://example.com/final",
        "status": 200,
        "title": "Example",
        "engine": "webfetch",
        "return_type": return_type,
        "selectors": [],
        "selected_element_count": None,
        "content": content,
        "content_length": len(content),
        "source_html_length": len(content),
        "content_source": content_source or f"rendered_dom_{return_type}",
        "error": None,
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_render_web_page_html_returns_rendered_dom_html() -> None:
    """网页浏览工具将 webfetch HTML 响应映射为旧返回结构。"""
    fetch_mock = AsyncMock(
        return_value=_webfetch_response(
            "<html><body>Example rendered page</body></html>",
            final_url="https://example.com/final",
        )
    )

    with patch("app.ai.agentic.tooling.browser_tool._fetch_web_page", fetch_mock):
        result = await render_web_page_html(
            "example.com",
            wait_after_ms=0,
        )

    assert result == {
        "url": "https://example.com",
        "final_url": "https://example.com/final",
        "status": 200,
        "title": "Example",
        "html": "<html><body>Example rendered page</body></html>",
        "html_length": 47,
        "content_source": "rendered_dom_html",
        "content_format": "html",
        "content_selectors": [],
        "selected_element_count": None,
    }
    fetch_mock.assert_awaited_once_with(
        {
            "url": "https://example.com",
            "return_type": "html",
            "selectors": [],
            "timeout_ms": 60_000,
            "wait_after_ms": 0,
        }
    )


@pytest.mark.asyncio
async def test_render_web_page_html_normalizes_host_with_port() -> None:
    """网页浏览工具保留 URL 主机端口并传给 webfetch 服务。"""
    fetch_mock = AsyncMock(
        return_value=_webfetch_response(
            "<html></html>",
            final_url="https://example.com:443/path",
        )
    )

    with patch("app.ai.agentic.tooling.browser_tool._fetch_web_page", fetch_mock):
        result = await render_web_page_html("example.com:443/path", wait_after_ms=0)

    assert result["url"] == "https://example.com:443/path"
    fetch_mock.assert_awaited_once()
    assert fetch_mock.await_args.args[0]["url"] == "https://example.com:443/path"


@pytest.mark.asyncio
async def test_render_web_page_html_allows_local_addresses_to_webfetch() -> None:
    """网页浏览工具允许本地 HTTP 地址由 webfetch 服务访问。"""
    fetch_mock = AsyncMock(
        return_value=_webfetch_response(
            "<html></html>",
            final_url="http://127.0.0.1:8000",
        )
    )

    with patch("app.ai.agentic.tooling.browser_tool._fetch_web_page", fetch_mock):
        result = await render_web_page_html("http://127.0.0.1:8000", wait_after_ms=0)

    assert result["url"] == "http://127.0.0.1:8000"
    assert fetch_mock.await_args.args[0]["url"] == "http://127.0.0.1:8000"


@pytest.mark.asyncio
async def test_render_web_page_html_rejects_non_http_url_scheme() -> None:
    """网页浏览工具在调用 webfetch 前拒绝非 HTTP 协议。"""
    fetch_mock = AsyncMock()

    with patch("app.ai.agentic.tooling.browser_tool._fetch_web_page", fetch_mock):
        result = await render_web_page_html("file:///etc/passwd", wait_after_ms=0)

    assert result["url"] == "file:///etc/passwd"
    assert result["error"] == "only http and https URLs are supported"
    assert result["content_source"] == "rendered_dom_html"
    fetch_mock.assert_not_called()


@pytest.mark.asyncio
async def test_render_web_page_html_can_return_markdown_with_source_url() -> None:
    """网页浏览工具将 webfetch Markdown 响应映射为 markdown 字段。"""
    markdown = "# Example Title\n\nSource URL: https://example.com/final\n\n## Section\n\n[Link](https://example.com/a)"
    fetch_mock = AsyncMock(
        return_value=_webfetch_response(
            markdown,
            return_type="markdown",
            final_url="https://example.com/final",
            source_html_length=82,
        )
    )

    with patch("app.ai.agentic.tooling.browser_tool._fetch_web_page", fetch_mock):
        result = await render_web_page_html(
            "https://example.com",
            content_format="markdown",
            wait_after_ms=0,
        )

    assert result["content_format"] == "markdown"
    assert result["final_url"] == "https://example.com/final"
    assert result["markdown"].startswith("# Example Title\n\nSource URL: https://example.com/final")
    assert "## Section" in result["markdown"]
    assert "[Link](https://example.com/a)" in result["markdown"]
    assert result["source_html_length"] == 82


@pytest.mark.asyncio
async def test_render_web_page_html_can_select_multiple_content_regions_once() -> None:
    """网页浏览工具将清理后的 selector 传给 webfetch 服务。"""
    fetch_mock = AsyncMock(
        return_value=_webfetch_response(
            "# Example Title\n\n## Main\n\nNews",
            return_type="markdown",
            selected_element_count=2,
        )
    )

    with patch("app.ai.agentic.tooling.browser_tool._fetch_web_page", fetch_mock):
        result = await render_web_page_html(
            "https://example.com",
            content_format="markdown",
            wait_after_ms=0,
            content_selectors=[" main ", ".news"],
        )

    assert fetch_mock.await_args.args[0]["selectors"] == ["main", ".news"]
    assert result["content_selectors"] == ["main", ".news"]
    assert result["selected_element_count"] == 2
    assert "## Main" in result["markdown"]
    assert "News" in result["markdown"]


@pytest.mark.asyncio
async def test_render_web_page_html_returns_webfetch_error_without_logging_exception() -> None:
    """webfetch 返回业务失败时直接映射错误，不记录异常日志。"""
    fetch_mock = AsyncMock(
        return_value={
            "success": False,
            "url": "https://example.com",
            "engine": "webfetch",
            "return_type": "html",
            "selectors": [],
            "content_source": None,
            "error": "engine unavailable",
        }
    )

    with patch("app.ai.agentic.tooling.browser_tool._fetch_web_page", fetch_mock), \
         patch.object(browser_tool.logger, "exception") as log_exception:
        result = await render_web_page_html("https://example.com", wait_after_ms=0)

    assert result == {
        "url": "https://example.com",
        "error": "engine unavailable",
        "content_source": "rendered_dom_html",
    }
    log_exception.assert_not_called()


@pytest.mark.asyncio
async def test_render_web_page_html_returns_error_when_webfetch_request_fails() -> None:
    """webfetch HTTP 调用异常时返回兼容错误结构。"""
    fetch_mock = AsyncMock(side_effect=RuntimeError("webfetch unavailable"))

    with patch("app.ai.agentic.tooling.browser_tool._fetch_web_page", fetch_mock), \
         patch.object(browser_tool.logger, "exception") as log_exception:
        result = await render_web_page_html("https://example.com", wait_after_ms=0)

    assert result == {
        "url": "https://example.com",
        "error": "RuntimeError: webfetch unavailable",
        "content_source": "rendered_dom_html",
    }
    log_exception.assert_called_once()


def test_browse_web_page_html_tool_metadata() -> None:
    """LangChain 工具元数据保留原有参数。"""
    assert browse_web_page_html.name == "browse_web_page_html"
    assert "url" in browse_web_page_html.args
    assert "content_format" in browse_web_page_html.args
    assert "content_selectors" in browse_web_page_html.args
    assert "max_content_chars" not in browse_web_page_html.args
    assert "wait_until" not in browse_web_page_html.args


@pytest.mark.asyncio
async def test_browse_web_page_html_tool_ainvoke() -> None:
    """LangChain 工具调用仍委托 render_web_page_html。"""
    render_mock = AsyncMock(return_value={"status": 200, "html": "<html></html>"})

    with patch("app.ai.agentic.tooling.browser_tool.render_web_page_html", render_mock):
        result = await browse_web_page_html.ainvoke(
            {
                "url": "https://example.com",
                "wait_after_ms": 0,
                "content_selectors": ["main"],
            }
        )

    assert result == {"status": 200, "html": "<html></html>"}
    render_mock.assert_awaited_once_with(
        url="https://example.com",
        content_format="html",
        timeout_ms=60_000,
        wait_after_ms=0,
        content_selectors=["main"],
    )

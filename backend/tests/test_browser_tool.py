from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ai.agentic.tooling import browser_context, browser_tool
from app.ai.agentic.tooling.browser_tool import browse_web_page_html, close_browser_context, render_web_page_html


@pytest.fixture(autouse=True)
def reset_browser_context_singleton():
    browser_tool._browser_context = None
    browser_context.set_cached_browser_context(None)
    if hasattr(browser_context, "_active_pages"):
        browser_context._active_pages.clear()
    if hasattr(browser_context, "_browser_context_closing_reason"):
        browser_context._browser_context_closing_reason = None
    yield
    browser_tool._browser_context = None
    browser_context.set_cached_browser_context(None)
    if hasattr(browser_context, "_active_pages"):
        browser_context._active_pages.clear()
    if hasattr(browser_context, "_browser_context_closing_reason"):
        browser_context._browser_context_closing_reason = None


@pytest.mark.asyncio
async def test_render_web_page_html_returns_rendered_dom_html():
    fake_page = MagicMock()
    fake_page.url = "https://example.com/final"
    fake_page.goto = AsyncMock(return_value=SimpleNamespace(status=200))
    fake_page.wait_for_timeout = AsyncMock()
    fake_page.title = AsyncMock(return_value="Example")
    fake_page.content = AsyncMock(return_value="<html><body>Example rendered page</body></html>")
    fake_page.close = AsyncMock()
    close_page = fake_page.close

    fake_context = MagicMock()
    fake_context.new_page = AsyncMock(return_value=fake_page)
    fake_context.close = AsyncMock()

    with patch("app.ai.agentic.tooling.browser_context.launch_context_async", AsyncMock(return_value=fake_context)):
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
    close_page.assert_awaited_once()
    fake_context.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_render_web_page_html_normalizes_host_with_port():
    fake_page = MagicMock()
    fake_page.url = "https://example.com:443/path"
    fake_page.goto = AsyncMock(return_value=SimpleNamespace(status=200))
    fake_page.wait_for_timeout = AsyncMock()
    fake_page.title = AsyncMock(return_value="Example")
    fake_page.content = AsyncMock(return_value="<html></html>")
    fake_page.close = AsyncMock()

    fake_context = MagicMock()
    fake_context.new_page = AsyncMock(return_value=fake_page)
    fake_context.close = AsyncMock()

    with patch("app.ai.agentic.tooling.browser_context.launch_context_async", AsyncMock(return_value=fake_context)):
        result = await render_web_page_html("example.com:443/path", wait_after_ms=0)

    assert result["url"] == "https://example.com:443/path"
    fake_page.goto.assert_awaited_once_with(
        "https://example.com:443/path",
        wait_until="domcontentloaded",
        timeout=60_000,
    )


@pytest.mark.asyncio
async def test_render_web_page_html_allows_local_addresses_to_browser():
    fake_page = MagicMock()
    fake_page.url = "http://127.0.0.1:8000"
    fake_page.goto = AsyncMock(return_value=SimpleNamespace(status=200))
    fake_page.wait_for_timeout = AsyncMock()
    fake_page.title = AsyncMock(return_value="Local")
    fake_page.content = AsyncMock(return_value="<html></html>")
    fake_page.close = AsyncMock()

    fake_context = MagicMock()
    fake_context.new_page = AsyncMock(return_value=fake_page)
    fake_context.close = AsyncMock()

    with patch("app.ai.agentic.tooling.browser_context.launch_context_async", AsyncMock(return_value=fake_context)):
        result = await render_web_page_html("http://127.0.0.1:8000", wait_after_ms=0)

    assert result["url"] == "http://127.0.0.1:8000"
    fake_page.goto.assert_awaited_once_with(
        "http://127.0.0.1:8000",
        wait_until="domcontentloaded",
        timeout=60_000,
    )


@pytest.mark.asyncio
async def test_render_web_page_html_rejects_non_http_url_scheme():
    launch_context = AsyncMock()

    with patch("app.ai.agentic.tooling.browser_context.launch_context_async", launch_context):
        result = await render_web_page_html("file:///etc/passwd", wait_after_ms=0)

    assert result["url"] == "file:///etc/passwd"
    assert result["error"] == "only http and https URLs are supported"
    launch_context.assert_not_called()


@pytest.mark.asyncio
async def test_render_web_page_html_can_return_markdown_with_source_url():
    fake_page = MagicMock()
    fake_page.url = "https://example.com/final"
    fake_page.goto = AsyncMock(return_value=SimpleNamespace(status=200))
    fake_page.wait_for_timeout = AsyncMock()
    fake_page.title = AsyncMock(return_value="Example Title")
    fake_page.content = AsyncMock(
        return_value="<html><body><h2>Section</h2><a href='https://example.com/a'>Link</a></body></html>"
    )
    fake_page.close = AsyncMock()

    fake_context = MagicMock()
    fake_context.new_page = AsyncMock(return_value=fake_page)
    fake_context.close = AsyncMock()

    with patch("app.ai.agentic.tooling.browser_context.launch_context_async", AsyncMock(return_value=fake_context)):
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
async def test_render_web_page_html_can_select_multiple_content_regions_once():
    fake_page = MagicMock()
    fake_page.url = "https://example.com/final"
    fake_page.goto = AsyncMock(return_value=SimpleNamespace(status=200))
    fake_page.wait_for_timeout = AsyncMock()
    fake_page.title = AsyncMock(return_value="Example Title")
    fake_page.content = AsyncMock()
    fake_page.evaluate = AsyncMock(
        return_value={
            "html": "<main><h2>Main</h2></main>\n<section><p>News</p></section>",
            "selected_element_count": 2,
        }
    )
    fake_page.close = AsyncMock()

    fake_context = MagicMock()
    fake_context.new_page = AsyncMock(return_value=fake_page)
    fake_context.close = AsyncMock()

    with patch("app.ai.agentic.tooling.browser_context.launch_context_async", AsyncMock(return_value=fake_context)):
        result = await render_web_page_html(
            "https://example.com",
            content_format="markdown",
            wait_after_ms=0,
            content_selectors=[" main ", ".news"],
        )

    fake_page.content.assert_not_called()
    fake_page.evaluate.assert_awaited_once()
    assert fake_page.evaluate.await_args.args[1] == ["main", ".news"]
    assert result["content_selectors"] == ["main", ".news"]
    assert result["selected_element_count"] == 2
    assert "## Main" in result["markdown"]
    assert "News" in result["markdown"]


@pytest.mark.asyncio
async def test_render_web_page_html_reuses_browser_context():
    first_page = MagicMock()
    first_page.url = "https://example.com/one"
    first_page.goto = AsyncMock(return_value=SimpleNamespace(status=200))
    first_page.wait_for_timeout = AsyncMock()
    first_page.title = AsyncMock(return_value="One")
    first_page.content = AsyncMock(return_value="<html>one</html>")
    first_page.close = AsyncMock()
    close_first_page = first_page.close

    second_page = MagicMock()
    second_page.url = "https://example.com/two"
    second_page.goto = AsyncMock(return_value=SimpleNamespace(status=200))
    second_page.wait_for_timeout = AsyncMock()
    second_page.title = AsyncMock(return_value="Two")
    second_page.content = AsyncMock(return_value="<html>two</html>")
    second_page.close = AsyncMock()
    close_second_page = second_page.close

    fake_context = MagicMock()
    fake_context.new_page = AsyncMock(side_effect=[first_page, second_page])
    fake_context.close = AsyncMock()
    launch_context = AsyncMock(return_value=fake_context)

    with patch("app.ai.agentic.tooling.browser_context.launch_context_async", launch_context):
        first_result = await render_web_page_html("https://example.com/one", wait_after_ms=0)
        second_result = await render_web_page_html("https://example.com/two", wait_after_ms=0)

    assert first_result["title"] == "One"
    assert second_result["title"] == "Two"
    launch_context.assert_awaited_once()
    assert fake_context.new_page.await_count == 2
    close_first_page.assert_awaited_once()
    close_second_page.assert_awaited_once()
    fake_context.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_render_web_page_html_uses_shared_browser_context():
    fake_page = MagicMock()
    fake_page.url = "https://example.com"
    fake_page.goto = AsyncMock(return_value=SimpleNamespace(status=200))
    fake_page.wait_for_timeout = AsyncMock()
    fake_page.title = AsyncMock(return_value="Shared")
    fake_page.content = AsyncMock(return_value="<html>shared</html>")
    fake_page.close = AsyncMock()

    fake_context = MagicMock()
    fake_context.new_page = AsyncMock(return_value=fake_page)
    fake_context.close = AsyncMock()
    browser_context.set_cached_browser_context(fake_context)

    with patch("app.ai.agentic.tooling.browser_context.launch_context_async", AsyncMock()) as launch_context:
        result = await render_web_page_html("https://example.com", wait_after_ms=0)

    assert result["title"] == "Shared"
    launch_context.assert_not_called()
    assert browser_tool._browser_context is fake_context


@pytest.mark.asyncio
async def test_close_browser_context_closes_singleton_context():
    fake_context = MagicMock()
    fake_context.close = AsyncMock()
    browser_context.set_cached_browser_context(fake_context)
    browser_tool._browser_context = fake_context

    await close_browser_context()

    fake_context.close.assert_awaited_once()
    assert browser_tool._browser_context is None
    assert browser_context.get_cached_browser_context() is None


@pytest.mark.asyncio
async def test_render_web_page_html_logs_shutdown_page_close_without_error(monkeypatch):
    fake_page = MagicMock()
    fake_page.goto = AsyncMock(side_effect=RuntimeError("Page.goto: Target page has been closed"))
    fake_page.close = AsyncMock()

    fake_context = MagicMock()
    fake_context.new_page = AsyncMock(return_value=fake_page)
    fake_context.close = AsyncMock()
    monkeypatch.setattr(browser_context, "get_browser_closing_reason", lambda: "backend_reload")

    with patch("app.ai.agentic.tooling.browser_context.launch_context_async", AsyncMock(return_value=fake_context)), \
         patch.object(browser_tool.logger, "info") as log_info, \
         patch.object(browser_tool.logger, "exception") as log_exception:
        result = await render_web_page_html("https://example.com", wait_after_ms=0)

    assert result == {
        "url": "https://example.com",
        "error": "Browser context is closing: backend_reload",
        "content_source": "rendered_dom_html",
    }
    log_info.assert_called_once()
    log_exception.assert_not_called()


def test_browse_web_page_html_tool_metadata():
    assert browse_web_page_html.name == "browse_web_page_html"
    assert "url" in browse_web_page_html.args
    assert "content_format" in browse_web_page_html.args
    assert "content_selectors" in browse_web_page_html.args
    assert "max_content_chars" not in browse_web_page_html.args
    assert "wait_until" not in browse_web_page_html.args


@pytest.mark.asyncio
async def test_browse_web_page_html_tool_ainvoke():
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

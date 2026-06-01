import asyncio

import pytest

from app.ai.agentic.tooling import browser_context


class FakePage:
    """Fake browser page for page-limit tests."""

    def __init__(self) -> None:
        self.close_calls = 0
        self.close_reasons: list[str | None] = []

    async def close(self, *, reason: str | None = None) -> None:
        """Close the fake page."""
        self.close_calls += 1
        self.close_reasons.append(reason)


class FakeContext:
    """Fake browser context for page-limit tests."""

    def __init__(self) -> None:
        self.pages: list[FakePage] = []
        self.close_calls = 0

    async def new_page(self) -> FakePage:
        """Create a fake page."""
        page = FakePage()
        self.pages.append(page)
        return page

    async def close(self) -> None:
        """Close the fake context."""
        self.close_calls += 1


@pytest.fixture(autouse=True)
def reset_browser_context_singleton():
    browser_context.set_cached_browser_context(None)
    browser_context._page_slots = None
    if hasattr(browser_context, "_active_pages"):
        browser_context._active_pages.clear()
    if hasattr(browser_context, "_browser_context_closing_reason"):
        browser_context._browser_context_closing_reason = None
    yield
    browser_context.set_cached_browser_context(None)
    browser_context._page_slots = None
    if hasattr(browser_context, "_active_pages"):
        browser_context._active_pages.clear()
    if hasattr(browser_context, "_browser_context_closing_reason"):
        browser_context._browser_context_closing_reason = None


@pytest.mark.asyncio
async def test_open_browser_page_waits_when_page_limit_is_reached(monkeypatch) -> None:
    monkeypatch.setattr(browser_context.settings, "CLOAKBROWSER_MAX_PAGES", 2)
    fake_context = FakeContext()
    browser_context.set_cached_browser_context(fake_context)
    first_release = asyncio.Event()
    second_release = asyncio.Event()
    third_started = asyncio.Event()
    seen_pages: list[FakePage] = []

    async def hold_page(release: asyncio.Event, started: asyncio.Event | None = None) -> None:
        async with browser_context.open_browser_page() as page:
            seen_pages.append(page)
            if started is not None:
                started.set()
            await release.wait()

    first_page_task = asyncio.create_task(hold_page(first_release))
    second_page_task = asyncio.create_task(hold_page(second_release))
    third_page_task = asyncio.create_task(hold_page(asyncio.Event(), third_started))
    await asyncio.sleep(0)

    assert len(fake_context.pages) == 2
    assert not third_page_task.done()

    first_release.set()
    await asyncio.wait_for(third_started.wait(), timeout=1)

    assert len(fake_context.pages) == 3
    assert seen_pages[0].close_calls == 1

    second_release.set()
    third_page_task.cancel()
    await asyncio.gather(first_page_task, second_page_task, third_page_task, return_exceptions=True)
    assert seen_pages[1].close_calls == 1
    assert seen_pages[2].close_calls == 1


@pytest.mark.asyncio
async def test_open_browser_page_closes_page_on_exit(monkeypatch) -> None:
    monkeypatch.setattr(browser_context.settings, "CLOAKBROWSER_MAX_PAGES", 1)
    fake_context = FakeContext()
    browser_context.set_cached_browser_context(fake_context)

    async with browser_context.open_browser_page() as page:
        assert page.close_calls == 0

    assert page.close_calls == 1


@pytest.mark.asyncio
async def test_close_browser_context_closes_active_pages_with_reason(monkeypatch) -> None:
    monkeypatch.setattr(browser_context.settings, "CLOAKBROWSER_MAX_PAGES", 1)
    fake_context = FakeContext()
    browser_context.set_cached_browser_context(fake_context)

    async with browser_context.open_browser_page() as page:
        await browser_context.close_browser_context(reason="backend_reload")

        assert page.close_calls == 1
        assert page.close_reasons == ["backend_reload"]

    assert page.close_calls == 1
    assert fake_context.close_calls == 1
    assert browser_context.get_cached_browser_context() is None

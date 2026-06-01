from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from cloakbrowser import launch_context_async

from app.core.config import settings

DEFAULT_VIEWPORT = {"width": 1365, "height": 900}
DEFAULT_LOCALE = "zh-CN"
DEFAULT_TIMEZONE = "Asia/Shanghai"

_browser_context: Any | None = None
_browser_context_lock = asyncio.Lock()
_page_slots: asyncio.BoundedSemaphore | None = None
_active_pages: set[Any] = set()
_browser_context_closing_reason: str | None = None


async def _launch_default_browser_context() -> Any:
    return await launch_context_async(
        headless=True,
        viewport=DEFAULT_VIEWPORT,
        locale=DEFAULT_LOCALE,
        timezone=DEFAULT_TIMEZONE,
    )


def _configured_max_pages() -> int:
    return max(1, int(settings.CLOAKBROWSER_MAX_PAGES))


def _get_page_slots() -> asyncio.BoundedSemaphore:
    global _page_slots

    if _page_slots is None:
        _page_slots = asyncio.BoundedSemaphore(_configured_max_pages())
    return _page_slots


async def _get_or_launch_browser_context_locked() -> Any:
    global _browser_context

    if _browser_context_closing_reason:
        raise RuntimeError(f"CloakBrowser context is closing: {_browser_context_closing_reason}")
    if _browser_context is None:
        _browser_context = await _launch_default_browser_context()
    return _browser_context


async def get_browser_context() -> Any:
    """
    Return the process-level shared CloakBrowser context.

    Returns:
        A reusable browser context.
    """
    async with _browser_context_lock:
        return await _get_or_launch_browser_context_locked()


async def _close_browser_page(page: Any, reason: str | None = None) -> None:
    if reason is None:
        await page.close()
        return
    try:
        await page.close(reason=reason)
    except TypeError:
        await page.close()


@asynccontextmanager
async def open_browser_page() -> AsyncIterator[Any]:
    """Open one CloakBrowser page and close it when leaving the context."""
    page = None
    async with _get_page_slots():
        async with _browser_context_lock:
            context = await _get_or_launch_browser_context_locked()
            page = await context.new_page()
            _active_pages.add(page)
        try:
            yield page
        finally:
            if page is not None and page in _active_pages:
                _active_pages.discard(page)
                await _close_browser_page(page, _browser_context_closing_reason)


async def _close_active_pages(reason: str) -> None:
    pages = list(_active_pages)
    _active_pages.clear()
    for page in pages:
        await _close_browser_page(page, reason)


def get_browser_closing_reason() -> str | None:
    """Return the active browser close reason when shutdown is in progress."""
    return _browser_context_closing_reason


def get_cached_browser_context() -> Any | None:
    """Return the cached context without launching a browser."""
    return _browser_context


def set_cached_browser_context(context: Any | None) -> None:
    """Set the cached context for compatibility wrappers and tests."""
    global _browser_context
    global _browser_context_closing_reason

    _browser_context = context
    if context is not None:
        _browser_context_closing_reason = None


async def close_browser_context(reason: str = "browser_context_closed") -> None:
    """Close and clear the process-level shared CloakBrowser context."""
    global _browser_context
    global _browser_context_closing_reason

    async with _browser_context_lock:
        context = _browser_context
        _browser_context = None
        _browser_context_closing_reason = reason

    try:
        await _close_active_pages(reason)
        if context is not None:
            await context.close()
    finally:
        async with _browser_context_lock:
            if _browser_context is None:
                _browser_context_closing_reason = None

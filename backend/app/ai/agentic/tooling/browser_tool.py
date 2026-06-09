from __future__ import annotations

from typing import Any, Dict, Literal
from urllib.parse import urlparse, urlunparse

import httpx
from langchain.tools import tool

from app.core.config import settings
from app.core.logger import get_logger

logger = get_logger(__name__)

ContentFormat = Literal["html", "markdown"]


def _normalize_browser_url(raw_url: str) -> str:
    """
    标准化网页浏览 URL。

    Args:
        raw_url: 用户输入的 URL。

    Returns:
        标准化后的 HTTP/HTTPS URL。

    Raises:
        ValueError: URL 为空、协议不支持或缺少主机名。
    """
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


def _normalize_content_selectors(content_selectors: list[str] | None) -> list[str]:
    """
    清理内容提取 selector 列表。

    Args:
        content_selectors: 原始 CSS selector 列表。

    Returns:
        去除空白后的 selector 列表。
    """
    if not content_selectors:
        return []
    return [str(selector).strip() for selector in content_selectors if str(selector).strip()]


def _build_webfetch_payload(
    normalized_url: str,
    content_format: ContentFormat,
    timeout_ms: int,
    wait_after_ms: int,
    content_selectors: list[str] | None,
) -> dict[str, Any]:
    """
    构建发送给 webfetch 服务的请求体。

    Args:
        normalized_url: 标准化后的目标 URL。
        content_format: 返回内容格式。
        timeout_ms: 页面导航超时时间。
        wait_after_ms: 导航后的额外等待时间。
        content_selectors: 可选 CSS selector 列表。

    Returns:
        webfetch 服务请求体。
    """
    return {
        "url": normalized_url,
        "return_type": content_format,
        "selectors": _normalize_content_selectors(content_selectors),
        "timeout_ms": timeout_ms,
        "wait_after_ms": wait_after_ms,
    }


def _map_webfetch_response(
    payload: dict[str, Any],
    normalized_url: str,
    content_format: ContentFormat,
    content_selectors: list[str] | None,
) -> dict[str, Any]:
    """
    将 webfetch 响应映射为浏览工具原有返回结构。

    Args:
        payload: webfetch 服务响应体。
        normalized_url: 标准化后的目标 URL。
        content_format: 请求的内容格式。
        content_selectors: 原始 selector 列表。

    Returns:
        与旧浏览工具兼容的结果字典。
    """
    base_result = {
        "url": normalized_url,
        "final_url": payload.get("final_url") or normalized_url,
        "status": payload.get("status"),
        "title": payload.get("title") or "",
        "content_format": content_format,
        "content_selectors": _normalize_content_selectors(content_selectors),
        "selected_element_count": payload.get("selected_element_count"),
    }

    if not payload.get("success"):
        return {
            "url": normalized_url,
            "error": str(payload.get("error") or "webfetch failed"),
            "content_source": payload.get("content_source") or f"rendered_dom_{content_format}",
        }

    content = str(payload.get("content") or "")
    if content_format == "markdown":
        return {
            **base_result,
            "markdown": content,
            "markdown_length": int(payload.get("content_length") or len(content)),
            "source_html_length": payload.get("source_html_length"),
            "content_source": payload.get("content_source") or "rendered_dom_markdown",
        }

    return {
        **base_result,
        "html": content,
        "html_length": int(payload.get("content_length") or len(content)),
        "content_source": payload.get("content_source") or "rendered_dom_html",
    }


async def _fetch_web_page(payload: dict[str, Any]) -> dict[str, Any]:
    """
    调用独立 webfetch 服务渲染网页。

    Args:
        payload: webfetch 服务请求体。

    Returns:
        webfetch 服务响应 JSON。

    Raises:
        httpx.HTTPError: 请求失败或服务返回非 2xx 响应。
        ValueError: 响应体不是 JSON 对象。
    """
    base_url = settings.WEBFETCH_BASE_URL.rstrip("/")
    timeout = httpx.Timeout(settings.WEBFETCH_TIMEOUT_SECONDS)
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
        response = await client.post("/fetch", json=payload)
        response.raise_for_status()
        result = response.json()

    if not isinstance(result, dict):
        raise ValueError(f"webfetch response must be an object, got {type(result).__name__}")
    return result


def _build_error_result(normalized_url: str, error: str, content_format: ContentFormat) -> dict[str, Any]:
    """
    构建网页抓取失败结果。

    Args:
        normalized_url: 标准化后的目标 URL。
        error: 错误描述。
        content_format: 请求的内容格式。

    Returns:
        兼容浏览工具返回结构的失败结果。
    """
    return {
        "url": normalized_url,
        "error": error,
        "content_source": f"rendered_dom_{content_format}",
    }


def _log_render_failure(normalized_url: str, exc: Exception) -> None:
    """
    记录 webfetch 调用失败日志。

    Args:
        normalized_url: 标准化后的目标 URL。
        exc: 捕获到的异常。
    """
    logger.exception(
        "render_web_page_html failed",
        extra={"url": normalized_url, "error_type": type(exc).__name__},
    )


async def render_web_page_html(
    url: str,
    content_format: ContentFormat = "html",
    timeout_ms: int = 60_000,
    wait_after_ms: int = 10_000,
    content_selectors: list[str] | None = None,
) -> Dict[str, Any]:
    """
    调用独立 webfetch 服务渲染网页，并返回浏览器当前 DOM HTML 或 Markdown。

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
        return _build_error_result(url, str(exc), content_format)

    request_payload = _build_webfetch_payload(
        normalized_url=normalized_url,
        content_format=content_format,
        timeout_ms=timeout_ms,
        wait_after_ms=wait_after_ms,
        content_selectors=content_selectors,
    )
    try:
        response_payload = await _fetch_web_page(request_payload)
        return _map_webfetch_response(response_payload, normalized_url, content_format, content_selectors)
    except Exception as exc:
        _log_render_failure(normalized_url, exc)
        return _build_error_result(normalized_url, f"{type(exc).__name__}: {exc}", content_format)


@tool(parse_docstring=True)
async def browse_web_page_html(
    url: str,
    content_format: ContentFormat = "html",
    timeout_ms: int = 60_000,
    wait_after_ms: int = 5_000,
    content_selectors: list[str] | None = None,
) -> Dict[str, Any]:
    """
    调用 webfetch 服务打开网页，执行页面 JavaScript，并返回渲染后的 HTML 或 Markdown。

    Args:
        url: 要浏览的网页 URL；缺少协议时默认按 https:// 处理。
        content_format: 返回内容格式，默认html，可选markdown。html 返回浏览器当前 DOM HTML；markdown 将同一份渲染后 HTML 转成
            Markdown，并在 Markdown 开头写入 Source URL。
        timeout_ms: webfetch 服务的导航超时时间，单位毫秒。
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

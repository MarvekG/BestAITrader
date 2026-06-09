from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException

from app.config import get_settings
from app.engines.base import BrowserEngine
from app.engines.camoufox_engine import CamoufoxEngine
from app.engines.cloakbrowser_engine import CloakBrowserEngine
from app.engines.patchright_engine import PatchrightEngine
from app.schemas import EngineType, FetchRequest, FetchResponse, ReturnType
from app.services.cleaner import clean_markdown, compile_markdown_patterns, normalize_fetch_url
from app.services.renderer import convert_html_to_markdown

logger = logging.getLogger(__name__)


class EngineRegistry:
    """管理网页渲染引擎实例。"""

    def __init__(self) -> None:
        """初始化引擎注册表。"""
        settings = get_settings()
        self._engines: dict[EngineType, BrowserEngine] = {
            EngineType.CLOAKBROWSER: CloakBrowserEngine(settings.WEB_CLOAKBROWSER_MAX_PAGES),
            EngineType.PATCHRIGHT: PatchrightEngine(
                settings.WEB_PATCHRIGHT_MAX_PAGES,
                headless=settings.WEB_PATCHRIGHT_HEADLESS,
            ),
            EngineType.CAMOUFOX: CamoufoxEngine(
                settings.WEB_CAMOUFOX_MAX_PAGES,
                headless=settings.WEB_CAMOUFOX_HEADLESS,
            ),
        }

    def get(self, engine_type: EngineType) -> BrowserEngine:
        """
        获取指定类型的渲染引擎。

        Args:
            engine_type: 引擎类型。

        Returns:
            渲染引擎实例。
        """
        return self._engines[engine_type]

    async def close(self) -> None:
        """关闭所有渲染引擎资源。"""
        for engine in self._engines.values():
            await engine.close()


engine_registry = EngineRegistry()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """
    管理 FastAPI 应用生命周期。

    Args:
        _: FastAPI 应用实例。

    Yields:
        应用运行上下文。
    """
    yield
    await engine_registry.close()


app = FastAPI(title="Best AI Trader Web Fetcher", version="1.0.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    """
    返回服务健康状态。

    Returns:
        健康状态字典。
    """
    return {"status": "ok"}


def _build_error_response(request: FetchRequest, url: str, error: str) -> FetchResponse:
    """
    构建网页抓取错误响应。

    Args:
        request: 原始抓取请求。
        url: 响应中使用的 URL。
        error: 错误信息。

    Returns:
        统一错误响应。
    """
    return FetchResponse(
        success=False,
        url=url,
        engine=request.engine,
        return_type=request.return_type,
        selectors=request.selectors,
        error=error,
    )


@app.post("/fetch", response_model=FetchResponse)
async def fetch_page(request: FetchRequest) -> FetchResponse:
    """
    渲染网页并返回 HTML 或 Markdown 内容。

    Args:
        request: 网页抓取请求。

    Returns:
        网页抓取响应。

    Raises:
        HTTPException: Markdown 清理正则非法。
    """
    try:
        normalized_url = normalize_fetch_url(request.url)
    except ValueError as exc:
        return _build_error_response(request, request.url, str(exc))

    compiled_patterns: list[re.Pattern[str]] = []
    if request.return_type == ReturnType.MARKDOWN:
        try:
            compiled_patterns = compile_markdown_patterns(request.markdown_clean_regexes)
        except re.error as exc:
            raise HTTPException(status_code=400, detail=f"invalid markdown_clean_regexes: {exc}") from exc

    settings = get_settings()
    timeout_ms = request.timeout_ms if request.timeout_ms is not None else settings.WEB_DEFAULT_TIMEOUT_MS
    wait_after_ms = request.wait_after_ms if request.wait_after_ms is not None else settings.WEB_DEFAULT_WAIT_AFTER_MS

    try:
        rendered = await engine_registry.get(request.engine).render(
            url=normalized_url,
            selectors=request.selectors,
            timeout_ms=timeout_ms,
            wait_after_ms=wait_after_ms,
        )
    except Exception as exc:
        logger.exception(
            "web fetch render failed",
            extra={"url": normalized_url, "engine": request.engine.value, "error_type": type(exc).__name__},
        )
        return _build_error_response(request, normalized_url, f"{type(exc).__name__}: {exc}")

    html_length = len(rendered.html)
    if request.return_type == ReturnType.MARKDOWN:
        markdown = convert_html_to_markdown(rendered.html, rendered.title, rendered.final_url)
        markdown = clean_markdown(markdown, compiled_patterns)
        return FetchResponse(
            success=True,
            url=normalized_url,
            final_url=rendered.final_url,
            status=rendered.status,
            title=rendered.title,
            engine=request.engine,
            return_type=request.return_type,
            selectors=request.selectors,
            selected_element_count=rendered.selected_element_count,
            content=markdown,
            content_length=len(markdown),
            source_html_length=html_length,
            content_source="rendered_dom_markdown",
        )

    return FetchResponse(
        success=True,
        url=normalized_url,
        final_url=rendered.final_url,
        status=rendered.status,
        title=rendered.title,
        engine=request.engine,
        return_type=request.return_type,
        selectors=request.selectors,
        selected_element_count=rendered.selected_element_count,
        content=rendered.html,
        content_length=html_length,
        source_html_length=html_length,
        content_source="rendered_dom_html",
    )

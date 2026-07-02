from __future__ import annotations

import asyncio
import logging
import re
import time
from contextlib import asynccontextmanager
from io import BytesIO
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from app.config import get_settings
from app.engines.base import BrowserEngine, PdfDownloadEngine
from app.engines.camoufox_engine import CamoufoxEngine
from app.engines.cloakbrowser_engine import CloakBrowserEngine
from app.engines.patchright_engine import PatchrightEngine
from app.schemas import EngineType, FetchRequest, FetchResponse, PdfDownloadRequest, ReturnType
from app.services.cleaner import clean_markdown, compile_markdown_patterns, normalize_fetch_url
from app.services.limiter import EngineLimiter, EngineLimiterTimeoutError
from app.services.renderer import convert_html_to_markdown

logger = logging.getLogger(__name__)


def _elapsed_ms(started_at: float) -> int:
    """
    计算请求处理耗时。

    Args:
        started_at: 请求开始时的单调时钟时间。

    Returns:
        请求耗时毫秒数。
    """
    return max(0, round((time.perf_counter() - started_at) * 1000))


def _convert_rendered_html_to_markdown(
    html: str,
    title: str,
    source_url: str,
    compiled_patterns: list[re.Pattern[str]],
) -> str:
    """
    将渲染后的 HTML 转换并清理为 Markdown。

    Args:
        html: 渲染后的 HTML。
        title: 页面标题。
        source_url: 最终来源 URL。
        compiled_patterns: 已编译的 Markdown 清理正则。

    Returns:
        清理后的 Markdown 文本。
    """
    markdown = convert_html_to_markdown(html, title, source_url)
    return clean_markdown(markdown, compiled_patterns)


class EngineRegistry:
    """管理网页渲染引擎实例。"""

    def __init__(self) -> None:
        """初始化引擎注册表。"""
        settings = get_settings()
        limiter = EngineLimiter(settings.WEBFETCH_MAX_PAGES, settings.WEBFETCH_ENGINE_ACQUIRE_TIMEOUT_MS)
        self._engines: dict[EngineType, BrowserEngine] = {
            EngineType.CLOAKBROWSER: CloakBrowserEngine(limiter),
            EngineType.PATCHRIGHT: PatchrightEngine(
                limiter,
                headless=settings.WEBFETCH_PATCHRIGHT_HEADLESS,
            ),
            EngineType.CAMOUFOX: CamoufoxEngine(
                limiter,
                headless=settings.WEBFETCH_CAMOUFOX_HEADLESS,
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

    def get_pdf_downloader(self, engine_type: EngineType) -> PdfDownloadEngine:
        """
        获取 PDF 下载引擎。

        Args:
            engine_type: 引擎类型。

        Returns:
            支持 request context 下载的引擎。
        """
        engine = self._engines[engine_type]
        if not hasattr(engine, "download_pdf"):
            raise RuntimeError(f"PDF download engine is not available: {engine_type.value}")
        return engine

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
    settings = get_settings()
    logger.info(
        "webfetch service started",
        extra={
            "max_pages": settings.WEBFETCH_MAX_PAGES,
            "engine_acquire_timeout_ms": settings.WEBFETCH_ENGINE_ACQUIRE_TIMEOUT_MS,
            "default_timeout_ms": settings.WEBFETCH_DEFAULT_TIMEOUT_MS,
            "default_wait_after_ms": settings.WEBFETCH_DEFAULT_WAIT_AFTER_MS,
            "default_download_timeout_seconds": settings.WEBFETCH_DEFAULT_DOWNLOAD_TIMEOUT_SECONDS,
        },
    )
    try:
        yield
    finally:
        logger.info("webfetch service stopping", extra={"engine_count": len(engine_registry._engines)})
        await engine_registry.close()
        logger.info("webfetch service stopped")


app = FastAPI(title="Best AI Trader Webfetch", version="1.0.0", lifespan=lifespan)


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
    started_at = time.perf_counter()
    try:
        normalized_url = normalize_fetch_url(request.url)
    except ValueError as exc:
        logger.warning(
            "webfetch request rejected",
            extra={"reason": "invalid_url", "raw_url_length": len(request.url), "error_type": type(exc).__name__},
        )
        return _build_error_response(request, request.url, str(exc))

    compiled_patterns: list[re.Pattern[str]] = []
    if request.return_type == ReturnType.MARKDOWN:
        try:
            compiled_patterns = compile_markdown_patterns(request.markdown_clean_regexes)
        except re.error as exc:
            logger.warning(
                "webfetch request rejected",
                extra={
                    "reason": "invalid_markdown_clean_regexes",
                    "url": normalized_url,
                    "engine": request.engine.value,
                    "pattern_count": len(request.markdown_clean_regexes),
                    "error_type": type(exc).__name__,
                },
            )
            raise HTTPException(status_code=400, detail=f"invalid markdown_clean_regexes: {exc}") from exc

    settings = get_settings()
    timeout_ms = request.timeout_ms if request.timeout_ms is not None else settings.WEBFETCH_DEFAULT_TIMEOUT_MS
    wait_after_ms = request.wait_after_ms if request.wait_after_ms is not None else settings.WEBFETCH_DEFAULT_WAIT_AFTER_MS
    logger.info(
        "webfetch request started",
        extra={
            "url": normalized_url,
            "engine": request.engine.value,
            "return_type": request.return_type.value,
            "selector_count": len(request.selectors),
            "timeout_ms": timeout_ms,
            "wait_after_ms": wait_after_ms,
        },
    )

    try:
        rendered = await engine_registry.get(request.engine).render(
            url=normalized_url,
            selectors=request.selectors,
            timeout_ms=timeout_ms,
            wait_after_ms=wait_after_ms,
        )
    except EngineLimiterTimeoutError as exc:
        logger.warning(
            "webfetch request limited",
            extra={
                "url": normalized_url,
                "engine": request.engine.value,
                "return_type": request.return_type.value,
                "duration_ms": _elapsed_ms(started_at),
                "error_type": type(exc).__name__,
            },
        )
        return _build_error_response(request, normalized_url, f"{type(exc).__name__}: {exc}")
    except Exception as exc:
        logger.exception(
            "webfetch render failed",
            extra={
                "url": normalized_url,
                "engine": request.engine.value,
                "return_type": request.return_type.value,
                "duration_ms": _elapsed_ms(started_at),
                "error_type": type(exc).__name__,
            },
        )
        return _build_error_response(request, normalized_url, f"{type(exc).__name__}: {exc}")

    html_length = len(rendered.html)
    if request.return_type == ReturnType.MARKDOWN:
        markdown = await asyncio.to_thread(
            _convert_rendered_html_to_markdown,
            rendered.html,
            rendered.title,
            rendered.final_url,
            compiled_patterns,
        )
        response = FetchResponse(
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
    else:
        response = FetchResponse(
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

    logger.info(
        "webfetch request completed",
        extra={
            "url": normalized_url,
            "final_url": rendered.final_url,
            "status": rendered.status,
            "engine": request.engine.value,
            "return_type": request.return_type.value,
            "selector_count": len(request.selectors),
            "selected_element_count": rendered.selected_element_count,
            "content_length": response.content_length,
            "source_html_length": response.source_html_length,
            "duration_ms": _elapsed_ms(started_at),
        },
    )
    return response


@app.post("/download")
async def download_file(request: PdfDownloadRequest) -> StreamingResponse:
    """
    下载 PDF 并流式返回原始文件内容。

    Args:
        request: PDF 下载请求。

    Returns:
        原始 PDF 文件流。

    Raises:
        HTTPException: URL 非法或下载失败。
    """
    started_at = time.perf_counter()
    try:
        normalized_url = normalize_fetch_url(request.url)
    except ValueError as exc:
        logger.warning(
            "webfetch pdf download rejected",
            extra={"reason": "invalid_url", "raw_url_length": len(request.url), "error_type": type(exc).__name__},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    settings = get_settings()
    timeout = request.timeout if request.timeout is not None else settings.WEBFETCH_DEFAULT_DOWNLOAD_TIMEOUT_SECONDS
    logger.info(
        "webfetch pdf download started",
        extra={"url": normalized_url, "engine": request.engine.value, "timeout_seconds": timeout},
    )

    try:
        downloaded = await engine_registry.get_pdf_downloader(request.engine).download_pdf(normalized_url, timeout)
    except EngineLimiterTimeoutError as exc:
        logger.warning(
            "webfetch pdf download limited",
            extra={
                "url": normalized_url,
                "engine": request.engine.value,
                "duration_ms": _elapsed_ms(started_at),
                "error_type": type(exc).__name__,
            },
        )
        raise HTTPException(status_code=503, detail=f"{type(exc).__name__}: {exc}") from exc
    except Exception as exc:
        logger.exception(
            "webfetch pdf download failed",
            extra={
                "url": normalized_url,
                "engine": request.engine.value,
                "duration_ms": _elapsed_ms(started_at),
                "error_type": type(exc).__name__,
            },
        )
        raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}") from exc

    content_type = downloaded.content_type if "pdf" in downloaded.content_type.lower() else "application/pdf"
    logger.info(
        "webfetch pdf download completed",
        extra={
            "url": normalized_url,
            "final_url": downloaded.final_url,
            "status": downloaded.status,
            "engine": request.engine.value,
            "content_type": content_type,
            "content_length": len(downloaded.content),
            "duration_ms": _elapsed_ms(started_at),
        },
    )
    return StreamingResponse(
        BytesIO(downloaded.content),
        media_type=content_type,
        headers={
            "Content-Length": str(len(downloaded.content)),
            "X-Final-URL": downloaded.final_url,
            "X-Source-Status": str(downloaded.status or ""),
        },
    )

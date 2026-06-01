from __future__ import annotations

import asyncio
import os
import tempfile
from typing import Any, Dict, Literal

from langchain.tools import tool

from app.ai.agentic.tooling import browser_context
from app.ai.agentic.tooling.browser_tool import _normalize_browser_url
from app.core.logger import get_logger
from app.data.pdf_parser import PDFParserService


logger = get_logger(__name__)

PDFParseEngine = Literal["word", "pymupdf"]
DEFAULT_MAX_MARKDOWN_CHARS = 40_000


async def _download_pdf_with_cloakbrowser(url: str, timeout_ms: int) -> tuple[bytes, str, int | None, str]:
    normalized_url = _normalize_browser_url(url)
    context = await browser_context.get_browser_context()
    response = await context.request.get(normalized_url, timeout=timeout_ms)

    status = response.status
    final_url = response.url
    headers = response.headers or {}
    content_type = str(headers.get("content-type") or headers.get("Content-Type") or "")
    pdf_bytes = await response.body()

    if status and status >= 400:
        raise RuntimeError(f"PDF download failed with HTTP status {status}")
    if not pdf_bytes:
        raise RuntimeError("Downloaded PDF is empty")
    if not pdf_bytes.lstrip().startswith(b"%PDF"):
        raise RuntimeError(
            f"Downloaded content is not a valid PDF: content_type={content_type or 'unknown'} bytes={len(pdf_bytes)}"
        )
    if "pdf" not in content_type.lower():
        logger.warning(
            "downloaded content is not explicitly marked as PDF: url=%s content_type=%s",
            final_url,
            content_type,
        )

    return pdf_bytes, final_url, status, content_type


def _parse_pdf_file_to_clean_markdown(pdf_path: str, engine: PDFParseEngine) -> str:
    parser = PDFParserService()
    return parser.clean_markdown_content(parser.parse_pdf_to_markdown(pdf_path, engine=engine))


@tool(parse_docstring=True)
async def parse_pdf_to_markdown(
    url: str,
    engine: PDFParseEngine = "word",
    timeout_ms: int = 60_000,
    max_chars: int = DEFAULT_MAX_MARKDOWN_CHARS,
) -> Dict[str, Any]:
    """
    使用 CloakBrowser 下载 PDF，并将 PDF 解析为 Markdown。

    Args:
        url: 要下载和解析的 PDF URL；缺少协议时默认按 https:// 处理。
        engine: PDF 解析引擎。word 会先将 PDF 转为 Word，再转 Markdown；pymupdf 直接用 PyMuPDF 转 Markdown。
        timeout_ms: CloakBrowser 下载 PDF 的导航超时时间，单位毫秒。
        max_chars: 返回 Markdown 的最大字符数。默认 40000 字符，按 1 token 约 4 字符估算约等于 10000 token。

    Returns:
        包含最终 URL、HTTP 状态、解析引擎、Markdown 内容和截断标记的字典。
    """
    pdf_path = ""
    try:
        pdf_bytes, final_url, status, content_type = await _download_pdf_with_cloakbrowser(
            url=url,
            timeout_ms=max(1_000, int(timeout_ms or 60_000)),
        )
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as pdf_file:
            pdf_file.write(pdf_bytes)
            pdf_path = pdf_file.name

        markdown = await asyncio.to_thread(_parse_pdf_file_to_clean_markdown, pdf_path, engine)
        bounded_max_chars = max(1_000, min(int(max_chars or DEFAULT_MAX_MARKDOWN_CHARS), 200_000))
        truncated = len(markdown) > bounded_max_chars
        return {
            "status": "success",
            "url": url,
            "final_url": final_url,
            "http_status": status,
            "content_type": content_type,
            "engine": engine,
            "markdown": markdown[:bounded_max_chars],
            "markdown_length": len(markdown),
            "truncated": truncated,
            "content_source": (
                "cloakbrowser_pdf_word_markdown" if engine == "word" else "cloakbrowser_pdf_pymupdf_markdown"
            ),
        }
    except Exception as exc:
        logger.exception("parse_pdf_to_markdown failed: url=%s error=%s", url, exc)
        return {
            "status": "error",
            "url": url,
            "error": f"{type(exc).__name__}: {exc}",
            "content_source": "cloakbrowser_pdf_markdown",
        }
    finally:
        if pdf_path and os.path.exists(pdf_path):
            os.remove(pdf_path)

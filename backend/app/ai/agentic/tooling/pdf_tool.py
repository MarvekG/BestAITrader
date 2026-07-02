from __future__ import annotations

import asyncio
import os
import tempfile
from typing import Any, Dict, Literal

import httpx
from langchain.tools import tool

from app.ai.agentic.tooling.browser_tool import _normalize_browser_url
from app.core.config import settings
from app.core.logger import get_logger
from app.data.pdf_parser import PDFParserService


logger = get_logger(__name__)

PDFParseEngine = Literal["word", "pymupdf"]
DEFAULT_MAX_MARKDOWN_CHARS = 40_000


async def _download_pdf_with_webfetch(url: str, timeout: float) -> tuple[str, str, int | None, str]:
    """
    通过 webfetch 服务下载 PDF 原始文件并写入临时文件。

    Args:
        url: 要下载的 PDF URL。
        timeout: 下载超时时间，单位秒。

    Returns:
        PDF 临时文件路径、最终 URL、HTTP 状态和 Content-Type。

    Raises:
        RuntimeError: 下载失败、内容为空或内容不是有效 PDF。
    """
    normalized_url = _normalize_browser_url(url)
    base_url = settings.WEBFETCH_BASE_URL.rstrip("/")
    request_timeout = httpx.Timeout(settings.WEBFETCH_TIMEOUT_SECONDS)
    pdf_path = ""
    header_bytes = b""
    bytes_written = 0

    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=request_timeout) as client:
            async with client.stream("POST", "/download", json={"url": normalized_url, "timeout": timeout}) as response:
                response.raise_for_status()
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as pdf_file:
                    pdf_path = pdf_file.name
                    async for chunk in response.aiter_bytes():
                        if not chunk:
                            continue
                        if len(header_bytes) < 1024:
                            header_bytes += chunk[: 1024 - len(header_bytes)]
                        await asyncio.to_thread(pdf_file.write, chunk)
                        bytes_written += len(chunk)

        final_url = response.headers.get("x-final-url") or str(response.url)
        status_header = response.headers.get("x-source-status")
        status = int(status_header) if status_header and status_header.isdigit() else response.status_code
        content_type = str(response.headers.get("content-type") or "")

        if status and status >= 400:
            raise RuntimeError(f"PDF download failed with HTTP status {status}")
        if not bytes_written:
            raise RuntimeError("Downloaded PDF is empty")
        if not header_bytes.lstrip().startswith(b"%PDF"):
            raise RuntimeError(
                f"Downloaded content is not a valid PDF: content_type={content_type or 'unknown'} bytes={bytes_written}"
            )
        if "pdf" not in content_type.lower():
            logger.warning(
                "downloaded content is not explicitly marked as PDF",
                extra={"url": final_url, "content_type": content_type},
            )

        return pdf_path, final_url, status, content_type
    except Exception:
        if pdf_path and os.path.exists(pdf_path):
            os.remove(pdf_path)
        raise


def _parse_pdf_file_to_clean_markdown(pdf_path: str, engine: PDFParseEngine) -> str:
    parser = PDFParserService()
    return parser.clean_markdown_content(parser.parse_pdf_to_markdown(pdf_path, engine=engine))


@tool(parse_docstring=True)
async def parse_pdf_to_markdown(
    url: str,
    engine: PDFParseEngine = "word",
    timeout: float = 60.0,
    max_chars: int = DEFAULT_MAX_MARKDOWN_CHARS,
) -> Dict[str, Any]:
    """
    调用 webfetch 服务下载 PDF，并将 PDF 解析为 Markdown。

    Args:
        url: 要下载和解析的 PDF URL；缺少协议时默认按 https:// 处理。
        engine: PDF 解析引擎。word 会先将 PDF 转为 Word，再转 Markdown；pymupdf 直接用 PyMuPDF 转 Markdown。
        timeout: 传给 webfetch 服务的 PDF 下载超时时间，单位秒。
        max_chars: 返回 Markdown 的最大字符数。默认 40000 字符，按 1 token 约 4 字符估算约等于 10000 token。

    Returns:
        包含最终 URL、HTTP 状态、解析引擎、Markdown 内容和截断标记的字典。
    """
    pdf_path = ""
    try:
        pdf_path, final_url, status, content_type = await _download_pdf_with_webfetch(
            url=url,
            timeout=max(1.0, float(timeout or 60.0)),
        )
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
                "web_pdf_word_markdown" if engine == "word" else "web_pdf_pymupdf_markdown"
            ),
        }
    except Exception as exc:
        logger.exception(
            "parse_pdf_to_markdown failed",
            extra={"url": url, "error_type": type(exc).__name__},
        )
        return {
            "status": "error",
            "url": url,
            "error": f"{type(exc).__name__}: {exc}",
            "content_source": "web_pdf_markdown",
        }
    finally:
        if pdf_path and os.path.exists(pdf_path):
            os.remove(pdf_path)

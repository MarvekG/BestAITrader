import os

import pytest

from app.ai.agentic.tooling import pdf_tool
from app.ai.agentic.tooling.pdf_tool import DEFAULT_MAX_MARKDOWN_CHARS, parse_pdf_to_markdown
from app.data.pdf_parser import PDFParserService


@pytest.mark.asyncio
async def test_download_pdf_with_webfetch_rejects_pdf_viewer_html(monkeypatch):
    """PDF 下载工具拒绝 webfetch 服务返回的 HTML 内容。"""
    monkeypatch.setattr(pdf_tool.settings, "WEBFETCH_TIMEOUT_SECONDS", 123.0)

    class FakeResponse:
        status = 200
        status_code = 200
        headers = {
            "content-type": "application/pdf",
            "x-final-url": "https://example.com/report.pdf",
            "x-source-status": "200",
        }
        url = "http://webfetch:8010/download"

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        def raise_for_status(self) -> None:
            """模拟响应状态检查。"""

        async def aiter_bytes(self):
            """模拟流式返回 HTML 内容。"""
            yield b"<!doctype html><html></html>"

    class FakeClient:
        def __init__(self, base_url: str, timeout) -> None:
            self.base_url = base_url
            self.timeout = timeout
            assert timeout.connect == 123.0
            assert timeout.read == 123.0
            assert timeout.write == 123.0
            assert timeout.pool == 123.0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        def stream(self, method: str, path: str, json: dict[str, object]) -> FakeResponse:
            assert method == "POST"
            assert path == "/download"
            assert json == {"url": "https://example.com/report.pdf", "timeout": 5.0}
            return FakeResponse()

    monkeypatch.setattr(pdf_tool.httpx, "AsyncClient", FakeClient)

    with pytest.raises(RuntimeError, match="not a valid PDF"):
        await pdf_tool._download_pdf_with_webfetch("https://example.com/report.pdf", 5.0)


@pytest.mark.asyncio
async def test_download_pdf_with_webfetch_writes_stream_to_temp_file(monkeypatch):
    """PDF 下载工具将响应流直接写入临时文件。"""
    to_thread_calls = []

    async def fake_to_thread(func, *args):
        to_thread_calls.append((func, args))
        return func(*args)

    class FakeResponse:
        status = 200
        status_code = 200
        headers = {
            "content-type": "application/pdf",
            "x-final-url": "https://example.com/report.pdf",
            "x-source-status": "200",
        }
        url = "http://webfetch:8010/download"

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        def raise_for_status(self) -> None:
            """模拟响应状态检查。"""

        async def aiter_bytes(self):
            """模拟被拆分的 PDF 响应流。"""
            yield b"%P"
            yield b"DF-1.7 fake"

    class FakeClient:
        def __init__(self, base_url: str, timeout) -> None:
            self.base_url = base_url
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        def stream(self, method: str, path: str, json: dict[str, object]) -> FakeResponse:
            assert method == "POST"
            assert path == "/download"
            assert json == {"url": "https://example.com/report.pdf", "timeout": 7.5}
            return FakeResponse()

    monkeypatch.setattr(pdf_tool.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(pdf_tool.asyncio, "to_thread", fake_to_thread)

    pdf_path, final_url, status, content_type = await pdf_tool._download_pdf_with_webfetch(
        "https://example.com/report.pdf",
        7.5,
    )
    try:
        assert final_url == "https://example.com/report.pdf"
        assert status == 200
        assert content_type == "application/pdf"
        with open(pdf_path, "rb") as pdf_file:
            assert pdf_file.read() == b"%PDF-1.7 fake"
        assert to_thread_calls[0][1] == (b"%P",)
        assert to_thread_calls[1][1] == (b"DF-1.7 fake",)
        assert len(to_thread_calls) == 2
    finally:
        if os.path.exists(pdf_path):
            os.remove(pdf_path)


@pytest.mark.asyncio
async def test_parse_pdf_to_markdown_tool_uses_word_pipeline(monkeypatch):
    async def fake_download(url: str, timeout: float):
        assert url == "https://example.com/report.pdf"
        assert timeout == 5.0
        return "/tmp/fake-report.pdf", "https://example.com/final.pdf", 200, "application/pdf"

    class FakePDFParserService:
        def parse_pdf_to_markdown(self, pdf_path: str, engine: str = "word") -> str:
            assert pdf_path.endswith(".pdf")
            assert engine == "word"
            return "# Report\n\n" + ("Revenue and profit.\n" * 200)

        def clean_markdown_content(self, markdown: str) -> str:
            return markdown.strip()

    monkeypatch.setattr(pdf_tool, "_download_pdf_with_webfetch", fake_download)
    monkeypatch.setattr(pdf_tool, "PDFParserService", FakePDFParserService)

    result = await parse_pdf_to_markdown.ainvoke(
        {
            "url": "https://example.com/report.pdf",
            "engine": "word",
            "timeout": 5.0,
            "max_chars": 1200,
        }
    )

    assert result["status"] == "success"
    assert result["final_url"] == "https://example.com/final.pdf"
    assert result["engine"] == "word"
    assert result["markdown"].startswith("# Report")
    assert result["markdown_length"] > len(result["markdown"])
    assert result["truncated"] is True
    assert result["content_source"] == "web_pdf_word_markdown"


@pytest.mark.asyncio
async def test_parse_pdf_to_markdown_default_limit_is_about_10000_tokens(monkeypatch):
    async def fake_download(url: str, timeout: float):
        return "/tmp/fake-report.pdf", url, 200, "application/pdf"

    monkeypatch.setattr(pdf_tool, "_download_pdf_with_webfetch", fake_download)
    monkeypatch.setattr(
        pdf_tool,
        "_parse_pdf_file_to_clean_markdown",
        lambda pdf_path, engine: "x" * (DEFAULT_MAX_MARKDOWN_CHARS + 100),
    )

    result = await parse_pdf_to_markdown.ainvoke({"url": "https://example.com/report.pdf"})

    assert result["status"] == "success"
    assert len(result["markdown"]) == 40_000
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_parse_pdf_to_markdown_runs_parser_in_thread(monkeypatch):
    async def fake_download(url: str, timeout: float):
        return "/tmp/fake-report.pdf", url, 200, "application/pdf"

    async def fake_to_thread(func, *args):
        assert func is pdf_tool._parse_pdf_file_to_clean_markdown
        assert args[1] == "word"
        return "# Threaded report\n\nRevenue and profit."

    monkeypatch.setattr(pdf_tool, "_download_pdf_with_webfetch", fake_download)
    monkeypatch.setattr(pdf_tool.asyncio, "to_thread", fake_to_thread)

    result = await parse_pdf_to_markdown.ainvoke(
        {
            "url": "https://example.com/report.pdf",
            "engine": "word",
            "timeout": 5.0,
            "max_chars": 5000,
        }
    )

    assert result["status"] == "success"
    assert result["engine"] == "word"
    assert result["markdown"].startswith("# Threaded report")


def test_pdf_parser_cleans_pdf2docx_page_state_files(tmp_path, monkeypatch):
    keep_path = tmp_path / "not-pages-0.json"
    page_path = tmp_path / "pages-0.json"
    keep_path.write_text("keep", encoding="utf-8")
    page_path.write_text("delete", encoding="utf-8")

    monkeypatch.chdir(tmp_path)

    PDFParserService()._cleanup_pdf2docx_page_state_files()

    assert keep_path.exists()
    assert not page_path.exists()

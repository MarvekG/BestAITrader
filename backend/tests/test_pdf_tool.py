import pytest

from app.ai.agentic.tooling import pdf_tool
from app.ai.agentic.tooling.pdf_tool import DEFAULT_MAX_MARKDOWN_CHARS, parse_pdf_to_markdown
from app.data.pdf_parser import PDFParserService


@pytest.mark.asyncio
async def test_download_pdf_with_cloakbrowser_rejects_pdf_viewer_html(monkeypatch):
    class FakeResponse:
        status = 200
        url = "https://example.com/report.pdf"
        headers = {"content-type": "application/pdf"}

        async def body(self) -> bytes:
            return b"<!doctype html><html></html>"

    class FakeRequest:
        async def get(self, url: str, timeout: int) -> FakeResponse:
            assert url == "https://example.com/report.pdf"
            assert timeout == 5000
            return FakeResponse()

    class FakeContext:
        request = FakeRequest()

    async def fake_get_browser_context() -> FakeContext:
        return FakeContext()

    monkeypatch.setattr(pdf_tool.browser_context, "get_browser_context", fake_get_browser_context)

    with pytest.raises(RuntimeError, match="not a valid PDF"):
        await pdf_tool._download_pdf_with_cloakbrowser("https://example.com/report.pdf", 5000)


@pytest.mark.asyncio
async def test_parse_pdf_to_markdown_tool_uses_word_pipeline(monkeypatch):
    async def fake_download(url: str, timeout_ms: int):
        assert url == "https://example.com/report.pdf"
        assert timeout_ms == 5000
        return b"%PDF-1.7 fake", "https://example.com/final.pdf", 200, "application/pdf"

    class FakePDFParserService:
        def parse_pdf_to_markdown(self, pdf_path: str, engine: str = "word") -> str:
            assert pdf_path.endswith(".pdf")
            assert engine == "word"
            return "# Report\n\n" + ("Revenue and profit.\n" * 200)

        def clean_markdown_content(self, markdown: str) -> str:
            return markdown.strip()

    monkeypatch.setattr(pdf_tool, "_download_pdf_with_cloakbrowser", fake_download)
    monkeypatch.setattr(pdf_tool, "PDFParserService", FakePDFParserService)

    result = await parse_pdf_to_markdown.ainvoke(
        {
            "url": "https://example.com/report.pdf",
            "engine": "word",
            "timeout_ms": 5000,
            "max_chars": 1200,
        }
    )

    assert result["status"] == "success"
    assert result["final_url"] == "https://example.com/final.pdf"
    assert result["engine"] == "word"
    assert result["markdown"].startswith("# Report")
    assert result["markdown_length"] > len(result["markdown"])
    assert result["truncated"] is True
    assert result["content_source"] == "cloakbrowser_pdf_word_markdown"


@pytest.mark.asyncio
async def test_parse_pdf_to_markdown_default_limit_is_about_10000_tokens(monkeypatch):
    async def fake_download(url: str, timeout_ms: int):
        return b"%PDF-1.7 fake", url, 200, "application/pdf"

    monkeypatch.setattr(pdf_tool, "_download_pdf_with_cloakbrowser", fake_download)
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
    async def fake_download(url: str, timeout_ms: int):
        return b"%PDF-1.7 fake", url, 200, "application/pdf"

    async def fake_to_thread(func, *args):
        assert func is pdf_tool._parse_pdf_file_to_clean_markdown
        assert args[1] == "word"
        return "# Threaded report\n\nRevenue and profit."

    monkeypatch.setattr(pdf_tool, "_download_pdf_with_cloakbrowser", fake_download)
    monkeypatch.setattr(pdf_tool.asyncio, "to_thread", fake_to_thread)

    result = await parse_pdf_to_markdown.ainvoke(
        {
            "url": "https://example.com/report.pdf",
            "engine": "word",
            "timeout_ms": 5000,
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

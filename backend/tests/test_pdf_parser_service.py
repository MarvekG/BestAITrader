import sys
import types
from pathlib import Path

from app.data.pdf_parser import PDFParserService


def test_parse_via_word_retries_without_multiprocessing_for_unicode_font_error(
    monkeypatch,
    tmp_path,
):
    service = PDFParserService()
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 test")

    convert_calls = []

    class FakeConverter:
        def __init__(self, input_path):
            assert input_path == str(pdf_path)

        def convert(self, output_path, start=0, multi_processing=True):
            convert_calls.append(
                {
                    "output_path": output_path,
                    "start": start,
                    "multi_processing": multi_processing,
                }
            )
            if multi_processing:
                raise ValueError("bytes must be in range(0, 256)")

            Path(output_path).write_bytes(b"fake-docx")

        def close(self):
            return None

    fake_pdf2docx = types.SimpleNamespace(Converter=FakeConverter)
    fake_mammoth = types.SimpleNamespace(
        convert_to_html=lambda _file: types.SimpleNamespace(value="<p>converted</p>")
    )
    fake_markdownify = types.SimpleNamespace(
        markdownify=lambda html, heading_style="ATX": "converted markdown"
    )

    monkeypatch.setitem(sys.modules, "pdf2docx", fake_pdf2docx)
    monkeypatch.setitem(sys.modules, "mammoth", fake_mammoth)
    monkeypatch.setitem(sys.modules, "markdownify", fake_markdownify)

    markdown = service._parse_via_word(str(pdf_path))

    assert markdown == "converted markdown"
    assert [call["multi_processing"] for call in convert_calls] == [True, False]
    assert not pdf_path.with_suffix(".docx").exists()

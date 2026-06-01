"""PDF parsing utilities."""

import logging
import os
import re
from pathlib import Path


logger = logging.getLogger(__name__)

PDF2DOCX_PAGE_STATE_PATTERN = "pages-*.json"


class PDFParserService:
    """Parse local PDF files into Markdown."""

    def parse_pdf_to_markdown(self, pdf_path: str, engine: str = "word") -> str:
        """
        Parse a local PDF file into Markdown.

        Args:
            pdf_path: Local PDF file path.
            engine: Parsing engine. Supports ``word`` and ``pymupdf``.

        Returns:
            Markdown text extracted from the PDF.

        Raises:
            FileNotFoundError: The PDF file does not exist.
            ValueError: The parsed content is empty or too short.
            Exception: The underlying parser failed.
        """
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")

        logger.info("Starting PDF parsing with %s: %s", engine, pdf_path)

        try:
            if engine == "word":
                markdown_text = self._parse_via_word(pdf_path)
            else:
                import pymupdf4llm

                markdown_text = pymupdf4llm.to_markdown(
                    pdf_path,
                    show_progress=False,
                )

            if not markdown_text or len(markdown_text.strip()) < 50:
                raise ValueError("Parsing result is empty or too short")

            logger.info(
                "PDF parsed successfully, engine: %s, length: %s chars",
                engine,
                len(markdown_text),
            )
            return markdown_text
        except Exception as exc:
            logger.error(
                "PDF parsing failed, engine: %s, path: %s, error: %s",
                engine,
                pdf_path,
                exc,
            )
            raise

    def _parse_via_word(self, pdf_path: str) -> str:
        """
        Parse a PDF via a Word intermediate file.

        Args:
            pdf_path: Local PDF file path.

        Returns:
            Markdown text extracted from the PDF.
        """
        docx_path = pdf_path.replace(".pdf", ".docx")
        if docx_path == pdf_path:
            docx_path += ".docx"

        converter = None
        try:
            logger.info("Converting PDF to Word: %s", pdf_path)
            from pdf2docx import Converter

            try:
                converter = Converter(pdf_path)
                converter.convert(docx_path, start=0, multi_processing=True)
            except Exception as exc:
                if "bytes must be in range(0, 256)" not in str(exc):
                    raise

                logger.warning(
                    "pdf2docx multiprocessing failed for %s, retrying in single process: %s",
                    pdf_path,
                    exc,
                )
                if converter is not None:
                    converter.close()
                    converter = None
                if os.path.exists(docx_path):
                    os.remove(docx_path)

                converter = Converter(pdf_path)
                converter.convert(docx_path, start=0, multi_processing=False)

            logger.info("Converting Word to Markdown: %s", docx_path)
            import mammoth
            from markdownify import markdownify as md

            with open(docx_path, "rb") as docx_file:
                result = mammoth.convert_to_html(docx_file)
                return md(result.value, heading_style="ATX")
        finally:
            if converter is not None:
                converter.close()
            if os.path.exists(docx_path):
                os.remove(docx_path)
                logger.debug("Temporary Word file deleted: %s", docx_path)
            self._cleanup_pdf2docx_page_state_files()

    def _cleanup_pdf2docx_page_state_files(self) -> None:
        """Remove temporary page state files created by pdf2docx multiprocessing."""
        for path in Path.cwd().glob(PDF2DOCX_PAGE_STATE_PATTERN):
            if path.is_file():
                try:
                    path.unlink()
                    logger.debug("Temporary pdf2docx page state file deleted: %s", path)
                except OSError as exc:
                    logger.warning("Failed to delete temporary pdf2docx page state file %s: %s", path, exc)

    def clean_markdown_content(self, markdown: str) -> str:
        """
        Normalize Markdown whitespace.

        Args:
            markdown: Raw Markdown content.

        Returns:
            Cleaned Markdown content.
        """
        if not markdown:
            return ""

        cleaned = re.sub(r"\n{4,}", "\n\n\n", markdown)
        cleaned = "\n".join(line.rstrip() for line in cleaned.split("\n"))
        return cleaned.strip()

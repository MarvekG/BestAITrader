#!/usr/bin/env python3
"""
Fetch SDK documentation pages for this skill.

Supported inputs:
- stdin JSON:
  {"url": "https://example.com/docs", "output_path": "references/sdk.md"}
- argv JSON:
  python scripts/fetch_sdk_docs.py '{"url":"https://example.com/docs"}'
- flags:
  python scripts/fetch_sdk_docs.py --url https://example.com/docs --output-path references/sdk.md
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_TIMEOUT_SECONDS = 30
USER_AGENT = "Best-AI-Trader-Skills-Loader/1.0"


class HtmlTextExtractor(HTMLParser):
    """Extract readable text from an HTML document."""

    BLOCK_TAGS = {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "ul",
    }

    def __init__(self) -> None:
        """Initialize parser state."""
        super().__init__(convert_charrefs=True)
        self._pieces: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Handle start tags."""
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
            return
        if tag in self.BLOCK_TAGS:
            self._pieces.append("\n")

    def handle_endtag(self, tag: str) -> None:
        """Handle end tags."""
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag in self.BLOCK_TAGS:
            self._pieces.append("\n")

    def handle_data(self, data: str) -> None:
        """Collect visible text."""
        if self._skip_depth:
            return
        text = data.strip()
        if text:
            self._pieces.append(text)
            self._pieces.append(" ")

    def text(self) -> str:
        """Return normalized extracted text."""
        raw_text = html.unescape("".join(self._pieces))
        raw_text = re.sub(r"[ \t\r\f\v]+", " ", raw_text)
        raw_text = re.sub(r"\n\s*\n\s*\n+", "\n\n", raw_text)
        lines = [line.strip() for line in raw_text.splitlines()]
        return "\n".join(line for line in lines if line).strip()


def parse_json_object(raw_payload: str, source: str) -> dict[str, Any]:
    """Parse a JSON object from text."""
    payload = json.loads(raw_payload)
    if not isinstance(payload, dict):
        raise ValueError(f"{source} JSON payload must be an object.")
    return payload


def read_payload() -> dict[str, Any]:
    """Read payload from argv or stdin."""
    parser = argparse.ArgumentParser(description="Fetch SDK documentation pages.")
    parser.add_argument("payload", nargs="?", help="JSON object payload.")
    parser.add_argument("--url", action="append", dest="urls")
    parser.add_argument("--output-path", dest="output_path")
    parser.add_argument("--format", choices=["text", "html"], default="text")
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args()

    if args.payload:
        return parse_json_object(args.payload, "argv payload")

    if args.urls:
        return {
            "urls": args.urls,
            "output_path": args.output_path,
            "format": args.format,
            "timeout_seconds": args.timeout_seconds,
        }

    raw_stdin = sys.stdin.read().strip()
    if raw_stdin:
        return parse_json_object(raw_stdin, "stdin")

    raise ValueError("Provide stdin JSON, argv JSON, or --url.")


def normalize_urls(payload: dict[str, Any]) -> list[str]:
    """Read one or more URLs from payload."""
    urls_value = payload.get("urls", payload.get("url"))
    if isinstance(urls_value, str):
        urls = [urls_value]
    elif isinstance(urls_value, list):
        urls = [str(item) for item in urls_value]
    else:
        raise ValueError("url or urls is required.")

    normalized = [url.strip() for url in urls if url.strip()]
    if not normalized:
        raise ValueError("url or urls must contain at least one URL.")
    for url in normalized:
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"Only http/https URLs are supported: {url}")
    return normalized


def fetch_url(url: str, timeout_seconds: int) -> tuple[str, str]:
    """Fetch one URL and return decoded text plus content type."""
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            content_type = response.headers.get("Content-Type", "")
            charset = response.headers.get_content_charset() or "utf-8"
            content = response.read().decode(charset, errors="replace")
            return content, content_type
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} while fetching {url}") from exc
    except URLError as exc:
        raise RuntimeError(f"Failed to fetch {url}: {exc.reason}") from exc


def to_text(content: str, content_type: str, output_format: str) -> str:
    """Convert fetched content into the requested output format."""
    if output_format == "html":
        return content
    if "html" not in content_type.lower() and not re.search(r"<html|<body|<div|<p", content, re.I):
        return content.strip()

    parser = HtmlTextExtractor()
    parser.feed(content)
    return parser.text()


def resolve_output_path(output_path: str | None) -> Path | None:
    """Resolve an optional skill-local output path safely."""
    if not output_path:
        return None

    skill_root = Path.cwd().resolve()
    raw_path = Path(output_path)
    if raw_path.is_absolute():
        raise ValueError("output_path must be relative to the skill root.")

    resolved_path = (skill_root / raw_path).resolve()
    try:
        resolved_path.relative_to(skill_root)
    except ValueError as exc:
        raise ValueError("output_path escapes the skill directory.") from exc
    return resolved_path


def build_document(payload: dict[str, Any]) -> dict[str, Any]:
    """Fetch all requested URLs and build a combined document."""
    urls = normalize_urls(payload)
    output_format = str(payload.get("format", "text")).strip() or "text"
    if output_format not in {"text", "html"}:
        raise ValueError("format must be either text or html.")

    timeout_seconds = max(1, int(payload.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)))
    pages = []
    for url in urls:
        content, content_type = fetch_url(url, timeout_seconds)
        pages.append(
            {
                "url": url,
                "content_type": content_type,
                "content": to_text(content, content_type, output_format),
            }
        )

    document = "\n\n".join(f"# {page['url']}\n\n{page['content']}" for page in pages)
    output_path = resolve_output_path(payload.get("output_path"))
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(document, encoding="utf-8")

    return {
        "success": True,
        "format": output_format,
        "page_count": len(pages),
        "pages": pages,
        "output_path": output_path.as_posix() if output_path else None,
    }


def main() -> int:
    """CLI entrypoint."""
    try:
        result = build_document(read_payload())
        sys.stdout.write(json.dumps(result, ensure_ascii=False))
        sys.stdout.write("\n")
        return 0
    except Exception as exc:
        sys.stdout.write(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False))
        sys.stdout.write("\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

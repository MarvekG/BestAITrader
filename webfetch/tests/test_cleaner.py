import re

import pytest

from app.services.cleaner import clean_markdown, compile_markdown_patterns, normalize_fetch_url


def test_normalize_fetch_url_adds_https_scheme() -> None:
    assert normalize_fetch_url("example.com/path") == "https://example.com/path"


def test_normalize_fetch_url_rejects_non_http_scheme() -> None:
    with pytest.raises(ValueError, match="only http and https URLs are supported"):
        normalize_fetch_url("file:///etc/passwd")


def test_compile_markdown_patterns_raises_for_invalid_regex() -> None:
    with pytest.raises(re.error):
        compile_markdown_patterns(["["])


def test_clean_markdown_applies_patterns_in_order() -> None:
    patterns = compile_markdown_patterns(["广告.*?结束", "\\n{2,}"])

    assert clean_markdown("正文\n广告内容结束\n\n尾部", patterns) == "正文尾部"
